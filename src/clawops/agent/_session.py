"""CallSession: per-call 상태 관리."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Callable, Awaitable, Literal

from clawops.agent._telemetry import CallMetrics

log = logging.getLogger("clawops.agent")


class CallSession:
    def __init__(
        self,
        *,
        call_id: str,
        from_number: str,
        to_number: str,
        account_id: str,
        direction: str = "inbound",
    ) -> None:
        self.call_id = call_id
        self.from_number = from_number
        self.to_number = to_number
        self.account_id = account_id
        self.direction = direction
        self.status: str = "queued" if direction == "outbound" else "ringing"
        # 서버가 통보한 최종 종료 상태. 통화가 끝나기 전에는 None.
        # completed(성사) / no-answer / busy / rejected / canceled / failed.
        self.ended_status: str | None = None
        self.start_time = datetime.now()
        self.metadata: dict[str, Any] = {}

        self._metrics = CallMetrics(_start_time_ms=time.time() * 1000)

        self._send_audio_fn: Callable[[bytes], Awaitable[None]] | None = None
        self._send_clear_fn: Callable[[], Awaitable[None]] | None = None
        self._hangup_fn: Callable[[], Awaitable[None]] | None = None

        self._event_handlers: dict[str, list[Callable[..., Awaitable[None]]]] = {}
        self._ended_event = asyncio.Event()

        # Transfer
        self._transfer_fn: Callable[[dict], Awaitable[dict]] | None = None

        # DTMF
        self._send_dtmf_fn: Callable[[str], Awaitable[None]] | None = None
        self._media_ws: Any | None = None  # is_connected 체크용
        self._dtmf_collector_active: bool = False
        self._dtmf_queue: asyncio.Queue[str] = asyncio.Queue()
        # 패시브 DTMF(=collect_dtmf 가 안 걸린 입력)의 debounce 버퍼. 통화별로 들고 있어야
        # 동시 통화의 키패드가 서로 섞이지 않는다. flush 는 ClawOpsAgent 가 돌린다 —
        # debounce 값이 에이전트 설정이라서다.
        self._passive_dtmf_buffer: list[str] = []
        self._passive_dtmf_task: asyncio.Task[None] | None = None

    def bind_transport(
        self,
        *,
        send_audio: Callable[[bytes], Awaitable[None]],
        send_clear: Callable[[], Awaitable[None]],
        hangup: Callable[[], Awaitable[None]],
        send_dtmf: Callable[[str], Awaitable[None]] | None = None,
        media_ws: Any | None = None,
        transfer: Callable[[dict], Awaitable[dict]] | None = None,
    ) -> None:
        """Wire the audio/DTMF/hangup transport for this call.

        Public composition point for servers that drive a call from an already-open
        media transport (e.g. a mediaUrl-dispatched worker) instead of going through
        ClawOpsAgent's control WS. Encapsulates the transport-binding fields so callers
        never assign the private ``_send_audio_fn`` / ``_media_ws`` / ... attributes.
        ``transfer`` is optional and only needed when the caller can service transfers.
        """
        self._send_audio_fn = send_audio
        self._send_clear_fn = send_clear
        self._hangup_fn = hangup
        self._send_dtmf_fn = send_dtmf
        self._media_ws = media_ws
        self._transfer_fn = transfer

    @property
    def metrics(self) -> CallMetrics:
        return self._metrics

    @property
    def duration(self) -> float:
        return (datetime.now() - self.start_time).total_seconds()

    async def send_audio(self, audio: bytes) -> None:
        if self._send_audio_fn:
            await self._send_audio_fn(audio)
            self._metrics.record_first_response()

    async def clear_audio(self) -> None:
        if self._send_clear_fn:
            await self._send_clear_fn()
            self._metrics.record_barge_in()

    async def hangup(self) -> None:
        if self._hangup_fn:
            await self._hangup_fn()

    def on(self, event: str, handler: Callable[..., Awaitable[None]]) -> None:
        self._event_handlers.setdefault(event, []).append(handler)

    async def wait(self) -> None:
        """통화가 종료될 때까지 대기한다."""
        await self._ended_event.wait()

    def _mark_ended(self, status: str | None = None) -> None:
        """통화 종료를 알린다. (내부 전용)

        Args:
            status: 서버가 통보한 최종 상태(completed/no-answer/busy/rejected/
                canceled/failed). 주어지면 그대로 확정한다 — 상대가 받지 않은 통화를
                'completed' 로 뭉개지 않기 위함이다. 생략하면 미디어 세션 정리 경로에서의
                호출이므로, 아직 종료 전일 때만 'completed' 로 채우고 이미 확정된 서버
                상태는 덮어쓰지 않는다.
        """
        if status is not None:
            self.status = status
            self.ended_status = status
        elif not self._ended_event.is_set():
            self.status = "completed"
            self.ended_status = "completed"
        # 아직 안 깨어난 패시브 DTMF flush 를 무효화한다. 취소하지 않는 이유는
        # _flush_passive_dtmf 주석 참고 — 이미 주입 중인 flush 를 자르지 않기 위해서다.
        self._passive_dtmf_task = None
        self._passive_dtmf_buffer.clear()
        self._ended_event.set()

    async def _emit(self, event: str, *args: Any) -> None:
        for handler in self._event_handlers.get(event, []):
            await handler(self, *args)

    def _route_dtmf(self, digit: str) -> None:
        """DTMF digit을 큐로 라우팅한다 (내부 전용).
        collector가 아직 활성화되기 전에 도착한 digit도 버퍼링한다."""
        self._dtmf_queue.put_nowait(digit)

    async def collect_dtmf(
        self,
        max_digits: int,
        finish_on_key: str = "#",
        timeout: float = 5,
        secure: bool = False,
    ) -> str:
        """DTMF 입력을 수집한다."""
        if self._dtmf_collector_active:
            raise RuntimeError("이미 DTMF 수집 중입니다")

        self._dtmf_collector_active = True
        # Don't create new queue — drain pre-buffered digits first
        collected: list[str] = []

        try:
            while len(collected) < max_digits:
                try:
                    digit = await asyncio.wait_for(self._dtmf_queue.get(), timeout=timeout)
                    if digit == finish_on_key:
                        break
                    collected.append(digit)
                except asyncio.TimeoutError:
                    while not self._dtmf_queue.empty() and len(collected) < max_digits:
                        d = self._dtmf_queue.get_nowait()
                        if d == finish_on_key:
                            break
                        collected.append(d)
                    break
        finally:
            self._dtmf_collector_active = False
            # Drain remaining queue to prevent stale digits in next collect
            while not self._dtmf_queue.empty():
                self._dtmf_queue.get_nowait()

        result = "".join(collected)
        if secure:
            log.info(f"DTMF collected: {'*' * len(result)} ({len(result)} digits, secure)")
        else:
            log.info(f"DTMF collected: {result}")
        return result

    async def transfer(
        self,
        to: str,
        *,
        destination_type: Literal["pstn", "sip"] = "pstn",
        mode: str = "blind",
        after_transfer: str = "terminate",
        hold_media: str = "ringback",
        whisper: str | None = None,
        context: dict | None = None,
        caller_id: str | None = None,
        caller_id_mode: Literal["account", "original"] | None = None,
        timeout: int = 30,
    ) -> dict:
        """Transfer the current call to a phone number or SIP endpoint.

        destination_type='pstn' (default): ``to`` is a phone number dialed via carrier.
        destination_type='sip': ``to`` is a SIP URI (e.g. ``sip:user@host``) connected
        directly to a SIP endpoint without going through the PSTN carrier. Requires the
        account to have an active ``sip_trunk`` add-on; otherwise the transfer fails and
        the call continues with the AI (result ``{"status": "failed", ...}``).

        전환받는 쪽에 표시되는 발신번호는 기본이 **계정 보유번호**(인바운드면 착신 070)다.

        ``caller_id_mode="original"`` 은 인바운드 통화의 **원 발신자 번호를 승계하려는 선호**다.
        승계할 수 없는 통화(KCT 직결 인입이 아니거나 국내 번호로 정규화되지 않는 발신번호)면
        조용히 계정 보유번호로 내려앉고 **전환은 그대로 성사된다**.

        ``caller_id`` 는 번호를 직접 주는 **지시**라 성격이 다르다. 허용 범위(계정 보유번호
        또는 KCT 직결 인입의 원 발신자)를 벗어나면 전환 자체가 실패한다. 둘 다 주면
        ``caller_id`` 가 이기고 ``caller_id_mode`` 는 무시된다 — 우선순위 판단은 서버가 한다.
        """
        if not self._transfer_fn:
            raise RuntimeError("transfer not available")
        # 서버는 'original' 만 특별 취급하고 나머지 값은 조용히 무시한다. 오타를 그대로
        # 흘려보내면 아무 에러 없이 계정 번호가 나가고, 개발자는 켰다고 믿는다 — 여기서 막는다.
        if caller_id_mode is not None and caller_id_mode not in ("account", "original"):
            raise ValueError(
                f"caller_id_mode must be 'account' or 'original', got {caller_id_mode!r}"
            )
        payload: dict[str, Any] = {
            "to": to,
            "destinationType": destination_type,
            "mode": mode,
            "afterTransfer": after_transfer,
            "holdMedia": hold_media,
            "whisper": whisper,
            "context": context,
            "callerId": caller_id,
            "timeout": timeout,
        }
        # 안 주면 키를 붙이지 않는다 — 구 서버와 기존 동작을 그대로 둔다(additive).
        if caller_id_mode is not None:
            payload["callerIdMode"] = caller_id_mode
        return await self._transfer_fn(payload)

    async def send_dtmf_sequence(self, digits: str) -> None:
        """여러 DTMF digit을 순서대로 전송한다."""
        if not self._send_dtmf_fn:
            raise RuntimeError("DTMF 전송 함수가 바인딩되지 않았습니다")
        for ch in digits:
            if self._media_ws and not self._media_ws.is_connected:
                raise ConnectionError("DTMF 전송 중 연결이 끊어졌습니다")
            if ch == "w":
                await asyncio.sleep(0.5)
            elif ch == "W":
                await asyncio.sleep(1.0)
            elif ch in "0123456789*#":
                await self._send_dtmf_fn(ch)
            else:
                raise ValueError(f"유효하지 않은 DTMF 문자: {ch}")
