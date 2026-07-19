"""Media WebSocket: per-call 오디오 스트림.

ClawOps VoiceML Stream 프로토콜을 구현한다:
connected -> start -> media (G.711 ulaw 8kHz base64) -> mark -> stop
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any, Callable, Awaitable

import aiohttp

log = logging.getLogger("clawops.agent")

# 죽은 피어(FIN/RST 없이 사라진 TCP: NAT idle timeout·네트워크 분단·콜노드 강제종료)를
# 감지하려면 WS keepalive 가 필요하다. 없으면 `stop` 프레임이 영영 안 와 수신 루프가 무한
# 블록되고, 서버 러너에서는 그 워커 슬롯이 영구 점유된다. aiohttp 는 ping 을 보내고 pong 이
# 이 시간 내 안 오면 연결을 닫는다(수신 루프가 깨져 정상 정리로 이어짐).
MEDIA_WS_HEARTBEAT_S = 30.0


def parse_start_event(data: dict[str, Any]) -> dict[str, Any]:
    start = data["start"]
    fmt = start.get("mediaFormat", {})
    return {
        "stream_id": start["streamId"],
        "call_id": start["callId"],
        "account_id": start.get("accountId", ""),
        "sample_rate": fmt.get("sampleRate", 8000),
    }


def parse_media_event(data: dict[str, Any]) -> dict[str, Any]:
    media = data["media"]
    return {
        "audio": base64.b64decode(media["payload"]),
        "timestamp": int(media.get("timestamp", 0)),
    }


def build_media_response(audio: bytes) -> dict[str, Any]:
    return {
        "event": "media",
        "media": {
            "payload": base64.b64encode(audio).decode(),
        },
    }


VALID_DTMF_DIGITS = set("0123456789*#")


def build_dtmf_message(digit: str) -> dict[str, Any]:
    if digit not in VALID_DTMF_DIGITS:
        raise ValueError(f"유효하지 않은 DTMF digit: {digit}")
    return {"event": "dtmf", "dtmf": {"digit": digit}}


def parse_dtmf_event(data: dict[str, Any]) -> dict[str, Any]:
    dtmf = data["dtmf"]
    return {
        "digit": dtmf["digit"],
        "track": dtmf.get("track", ""),
    }


class MediaWebSocket:
    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        on_audio: Callable[[bytes, int], Awaitable[None]],
        on_start: Callable[[dict[str, Any]], Awaitable[None]],
        on_stop: Callable[[], Awaitable[None]],
        on_dtmf: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._on_audio = on_audio
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_dtmf = on_dtmf
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._send_task: asyncio.Task[None] | None = None
        self._mark_waiters: dict[str, asyncio.Event] = {}

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(
            self._url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            heartbeat=MEDIA_WS_HEARTBEAT_S,
        )
        log.info(f"Media WS connected: {self._url}")
        self._send_task = asyncio.create_task(self._send_loop())

        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    event = data.get("event")

                    if event == "start":
                        await self._on_start(parse_start_event(data))
                    elif event == "media":
                        parsed = parse_media_event(data)
                        await self._on_audio(parsed["audio"], parsed["timestamp"])
                    elif event == "dtmf":
                        if self._on_dtmf:
                            await self._on_dtmf(parse_dtmf_event(data)["digit"])
                    elif event == "mark":
                        mark_name = data.get("mark", {}).get("name", "")
                        if mark_name in self._mark_waiters:
                            self._mark_waiters.pop(mark_name).set()
                    elif event == "stop":
                        await self._on_stop()
                        break
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
        finally:
            await self.close()

    async def send_audio(self, audio: bytes) -> None:
        self._audio_queue.put_nowait(audio)

    async def send_clear(self) -> None:
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        if self._ws and not self._ws.closed:
            await self._ws.send_str(json.dumps({"event": "clear"}))

    async def send_mark(self, name: str) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.send_str(json.dumps({
                "event": "mark",
                "mark": {"name": name},
            }))

    async def send_dtmf(self, digit: str) -> None:
        """단일 DTMF digit을 전송한다."""
        msg = build_dtmf_message(digit)
        if self._ws and not self._ws.closed:
            await self._ws.send_str(json.dumps(msg))
        else:
            log.warning(f"DTMF send skipped (WS not connected): {digit}")

    @property
    def is_connected(self) -> bool:
        """WebSocket이 연결되어 있는지 확인한다."""
        return self._ws is not None and not self._ws.closed

    async def flush(self) -> None:
        """Wait for all queued audio to be sent."""
        while not self._audio_queue.empty() and self.is_connected:
            await asyncio.sleep(0.005)

    async def wait_for_mark(self, name: str, timeout: float = 5.0) -> None:
        """Wait for a named mark to be echoed back by the server."""
        if not self.is_connected:
            return
        evt = asyncio.Event()
        self._mark_waiters[name] = evt
        try:
            await asyncio.wait_for(evt.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # 마크가 안 왔을 뿐 — 정상 반환한다(호출자에게 raise 하지 않는다).
            pass
        finally:
            # timeout·cancel·정상 완료 모두 waiter 를 회수한다.
            # (정상 echo 경로에서는 이미 pop 됐으므로 pop(name, None) 은 no-op.)
            self._mark_waiters.pop(name, None)

    async def _send_loop(self) -> None:
        """오디오 청크를 플랫폼으로 전송 (pacing은 플랫폼이 처리)."""
        try:
            while True:
                chunk = await self._audio_queue.get()
                if chunk is None:
                    break
                if self._ws and not self._ws.closed:
                    msg = build_media_response(chunk)
                    await self._ws.send_str(json.dumps(msg))
        except asyncio.CancelledError:
            pass

    async def graceful_close(self) -> None:
        """Drain queued audio, wait for it to play out, then close.

        The hangup handshake: flush the send queue, send a mark, wait for the
        server to echo it back (so trailing audio finishes playing), then close.
        Shared by ``ClawOpsAgent`` and mediaUrl-dispatched server runners so the
        hangup protocol lives in one place.
        """
        await self.flush()
        mark_name = f"hangup-{int(time.time() * 1000)}"
        await self.send_mark(mark_name)
        await self.wait_for_mark(mark_name, timeout=5.0)
        await self.close()

    async def close(self) -> None:
        self._audio_queue.put_nowait(None)
        if self._send_task and not self._send_task.done():
            self._send_task.cancel()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()
