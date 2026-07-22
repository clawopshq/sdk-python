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
        timeout: int = 30,
    ) -> dict:
        """Transfer the current call to a phone number or SIP endpoint.

        destination_type='pstn' (default): ``to`` is a phone number dialed via carrier.
        destination_type='sip': ``to`` is a SIP URI (e.g. ``sip:user@host``) connected
        directly to a SIP endpoint without going through the PSTN carrier. Requires the
        account to have an active ``sip_trunk`` add-on; otherwise the transfer fails and
        the call continues with the AI (result ``{"status": "failed", ...}``).
        """
        if not self._transfer_fn:
            raise RuntimeError("transfer not available")
        return await self._transfer_fn({
            "to": to,
            "destinationType": destination_type,
            "mode": mode,
            "afterTransfer": after_transfer,
            "holdMedia": hold_media,
            "whisper": whisper,
            "context": context,
            "callerId": caller_id,
            "timeout": timeout,
        })

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
