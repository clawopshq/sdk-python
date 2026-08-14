"""Control WebSocket: ClawOps 서버에 대한 상시 연결 관리.

Agent가 서버에 역방향으로 연결하여 인바운드 콜 알림을 수신한다.
자동 재연결(exponential backoff) 포함.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Callable, Awaitable
from urllib.parse import quote

import aiohttp

log = logging.getLogger("clawops.agent")

INITIAL_RECONNECT_DELAY = 1.0
MAX_RECONNECT_DELAY = 30.0

# 전환 결과를 기다리는 **방어선**. 정상 경로의 일부가 아니다 — 서버가 대상 응답 대기(transfer
# 파라미터 `timeout`)를 관리하고 결과 이벤트를 반드시 보내므로, 클라이언트는 브리지가 얼마나
# 길든 기다린다. 이 값에 도달하는 것은 "서버가 계약을 어겼다" 는 뜻이라 경고를 남긴다.
#
# 이 상한이 브리지 길이에 연동돼 있던 것이 2026-08-12 사고의 뿌리였다: timeout+10(=40초)로
# 기다리다 57초 브리지에서 취소되고, 뒤늦은 완료 이벤트가 취소된 future 를 건드려 제어 연결이
# 죽었다(16시간 수신 불가).
TRANSFER_RESULT_MAX_WAIT_S = 7200.0

# 통화 종료 통지를 받은 뒤 전환 대기를 정리하기까지의 유예. 즉시 정리하지 않는 이유는
# 이벤트 도착 순서가 보장되지 않기 때문이다 — 서버는 결과와 call.ended 를 각각 독립 HTTP 요청으로
# 릴레이하므로(call-engine agent-gw-relay: await 없는 POST) call.ended 가 먼저 닿을 수 있다.
# 순서를 보장하려 애쓰는 대신 이 유예로 흡수한다.
TRANSFER_LATE_ARRIVAL_GRACE_S = 2.0


def build_control_ws_url(*, base_url: str, account_id: str, number: str) -> str:
    scheme = "wss" if base_url.startswith("https") else "ws"
    host = base_url.replace("https://", "").replace("http://", "").rstrip("/")
    return f"{scheme}://{host}/v1/accounts/{account_id}/agent/listen?number={quote(number)}"


class ControlWebSocket:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        account_id: str,
        number: str,
        on_call_incoming: Callable[[dict[str, Any]], Awaitable[None]],
        on_call_ended: Callable[[dict[str, Any]], Awaitable[None]],
        on_call_outbound_ready: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_call_ringing: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_call_failed: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._url = build_control_ws_url(base_url=base_url, account_id=account_id, number=number)
        self._api_key = api_key
        self._on_call_incoming = on_call_incoming
        self._on_call_ended = on_call_ended
        self._on_call_outbound_ready = on_call_outbound_ready
        self._on_call_ringing = on_call_ringing
        self._on_call_failed = on_call_failed
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._running = False
        self._connected = asyncio.Event()
        # 전환 대기는 **요청 단위**로 키잉한다(callId 아님). return 모드는 실패 후 AI 로 돌아와
        # 같은 통화에서 전환을 다시 타므로(서버 transfer-handler 주석: 실측 8회 재시도), callId 로
        # 키잉하면 2차 요청이 1차 대기를 덮어써 1차가 영영 resolve 되지 않는다.
        self._pending_transfers: dict[str, asyncio.Future] = {}  # requestId → future
        # callId → 그 통화에서 대기 중인 requestId 집합. 통화 종료 시 정리 대상을 찾고,
        # requestId 를 echo 하지 않는 구 서버의 이벤트를 매핑하는 폴백에 쓴다.
        self._pending_by_call: dict[str, set[str]] = {}
        self._cleanup_timers: set[asyncio.TimerHandle] = set()

    async def wait_connected(self, timeout: float = 10.0) -> None:
        """Control WS 연결이 완료될 때까지 대기한다."""
        await asyncio.wait_for(self._connected.wait(), timeout)

    async def connect(self) -> None:
        self._running = True
        delay = INITIAL_RECONNECT_DELAY

        while self._running:
            try:
                self._connected.clear()
                self._session = aiohttp.ClientSession()
                self._ws = await self._session.ws_connect(
                    self._url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    heartbeat=30.0,
                )
                self._connected.set()
                log.info(f"Control WS connected: {self._url}")
                delay = INITIAL_RECONNECT_DELAY

                async for msg in self._ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        # 이벤트 처리 예외를 연결 수명과 분리한다. 핸들러 하나가 던진 예외가
                        # 이 루프 밖으로 새면 연결 태스크가 통째로 죽는다 — 2026-08-12 사고가
                        # 정확히 그것이었다(취소된 future 의 InvalidStateError).
                        try:
                            await self._dispatch(msg.data)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            log.exception("Control WS 이벤트 처리 실패 — 연결은 유지한다")
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                        break

            except asyncio.CancelledError:
                # close() 나 태스크 취소 — 재접속하지 않는다.
                raise
            except Exception as e:
                # 예외 종류로 재접속 여부를 가르지 않는다. 예전에는 aiohttp.ClientError/OSError
                # 만 잡아, 그 밖의 예외(InvalidStateError 등)에 재접속 없이 태스크가 죽었다.
                if "CERTIFICATE_VERIFY_FAILED" in str(e):
                    log.error(
                        "SSL 인증서 검증에 실패했습니다. "
                        "'pip install --upgrade certifi'를 실행해 보세요. "
                        "자세한 해결 방법: "
                        "https://github.com/learners-superpumped/clawops-python/blob/main/docs/agent/troubleshooting.md#ssl-인증서-에러-sslcertverificationerror"
                    )
                log.warning(f"Control WS error: {type(e).__name__}: {e}")
            finally:
                if self._ws and not self._ws.closed:
                    await self._ws.close()
                if self._session:
                    await self._session.close()
                self._ws = None
                self._session = None

            if self._running:
                log.info(f"Control WS reconnecting in {delay:.1f}s...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_DELAY)

    async def _dispatch(self, raw: str) -> None:
        """수신 메시지 하나를 해당 핸들러로 보낸다. 예외는 호출부가 격리한다."""
        data = json.loads(raw)
        event = data.get("event")
        if event == "call.incoming":
            await self._on_call_incoming(data)
        elif event == "call.ended":
            # 종료 통지는 전환 대기의 **정리 신호**다(결과를 만들어내는 신호가 아니다).
            # 순서가 보장되지 않으므로 유예를 두고 정리한다.
            self._schedule_transfer_cleanup(data.get("callId"))
            await self._on_call_ended(data)
        elif event == "call.outbound_ready" and self._on_call_outbound_ready:
            await self._on_call_outbound_ready(data)
        elif event == "call.ringing" and self._on_call_ringing:
            await self._on_call_ringing(data)
        elif event == "call.failed" and self._on_call_failed:
            await self._on_call_failed(data)
        elif event in (
            "call.transfer.started",
            "call.transfer.connected",
            "call.transfer.completed",
            "call.transfer.failed",
        ):
            self._on_transfer_event(data)

    async def request_transfer(self, call_id: str, transfer_params: dict) -> dict:
        """콜 전환을 요청하고 완료/실패 응답을 기다린다.

        `transfer_params['timeout']` 은 **대상 응답 대기**(서버가 관리)다. 여기서 기다리는 것은
        전환 통화가 끝날 때까지이며, 브리지 길이와 무관한 방어선
        (`TRANSFER_RESULT_MAX_WAIT_S`)만 둔다 — 서버가 결과 이벤트를 반드시 보낸다는 계약을
        신뢰하기 때문이다. 통화가 먼저 끝나면 종료 통지가 이 대기를 정리한다.
        """
        request_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_transfers[request_id] = future
        self._pending_by_call.setdefault(call_id, set()).add(request_id)
        try:
            await self._ws.send_str(
                json.dumps(
                    {
                        "event": "call.transfer",
                        "callId": call_id,
                        # 서버는 이 값을 해석하지 않고 결과 이벤트에 그대로 되돌린다.
                        "transfer": {**transfer_params, "requestId": request_id},
                    }
                )
            )
            return await asyncio.wait_for(future, timeout=TRANSFER_RESULT_MAX_WAIT_S)
        except asyncio.TimeoutError:
            log.error(
                f"전환 결과가 {TRANSFER_RESULT_MAX_WAIT_S:.0f}초 안에 오지 않았다: "
                f"call={call_id} request={request_id} — 서버가 결과 이벤트를 보내지 않았다는 뜻이다"
            )
            raise
        finally:
            # 어떤 경로로 끝나든 상관을 놓는다. 예전에는 타임아웃 시 dict 에 항목이 남아,
            # 뒤늦게 도착한 결과가 취소된 future 를 건드려 연결을 죽였다.
            self._discard_pending(call_id, request_id)

    def _discard_pending(self, call_id: str | None, request_id: str) -> None:
        self._pending_transfers.pop(request_id, None)
        if call_id and call_id in self._pending_by_call:
            self._pending_by_call[call_id].discard(request_id)
            if not self._pending_by_call[call_id]:
                del self._pending_by_call[call_id]

    def _resolve_request_id(self, data: dict) -> str | None:
        """이벤트가 어느 요청의 응답인지 판정한다.

        서버가 echo 한 `requestId` 가 정답이다. 없으면 callId 로 폴백한다 — requestId 를
        되돌리지 않는 **구 서버**와 섞이는 전환 기간용이다. 그 기간에는 같은 통화의 다중 전환을
        구분할 수 없다(대기가 하나뿐일 때만 매핑한다). 서버 배포가 끝나면 이 폴백은 제거 대상이다.
        """
        request_id = data.get("requestId")
        if request_id:
            return request_id if request_id in self._pending_transfers else None
        call_id = data.get("callId")
        waiting = self._pending_by_call.get(call_id or "")
        if waiting and len(waiting) == 1:
            return next(iter(waiting))
        return None

    def _on_transfer_event(self, data: dict) -> None:
        """전환 이벤트로 대기 중인 Future 를 resolve 한다."""
        if data.get("event") not in ("call.transfer.completed", "call.transfer.failed"):
            return  # started/connected 는 진행 알림 — 대기를 깨우지 않는다
        request_id = self._resolve_request_id(data)
        if not request_id:
            return
        future = self._pending_transfers.get(request_id)
        # done() 가드: 이미 취소/완료된 대기에 set_result 를 하면 InvalidStateError 가 나고,
        # 그 예외가 수신 루프를 죽였다(2026-08-12). 중복 도착도 여기서 조용히 흡수된다.
        if future is not None and not future.done():
            future.set_result(data.get("transfer", {}))
        self._discard_pending(data.get("callId"), request_id)

    def _schedule_transfer_cleanup(self, call_id: str | None) -> None:
        """통화 종료 후 유예를 두고 그 통화의 전환 대기를 정리한다."""
        if not call_id or call_id not in self._pending_by_call:
            return
        request_ids = list(self._pending_by_call[call_id])

        def _cleanup() -> None:
            for rid in request_ids:
                future = self._pending_transfers.get(rid)
                if future is not None and not future.done():
                    # 통화가 끝났는데 결과가 오지 않았다 — 대기를 놓아 호출부가 매달리지 않게 한다.
                    log.warning(f"통화 종료 후 전환 결과 미도착 — 대기 정리: call={call_id} request={rid}")
                    future.cancel()
                self._discard_pending(call_id, rid)

        loop = asyncio.get_event_loop()
        timer = loop.call_later(TRANSFER_LATE_ARRIVAL_GRACE_S, _cleanup)
        self._cleanup_timers.add(timer)

    async def send(self, data: dict[str, Any]) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.send_str(json.dumps(data))

    async def close(self) -> None:
        self._running = False
        for timer in self._cleanup_timers:
            timer.cancel()
        self._cleanup_timers.clear()
        for fut in self._pending_transfers.values():
            if not fut.done():
                fut.cancel()
        self._pending_transfers.clear()
        self._pending_by_call.clear()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()
