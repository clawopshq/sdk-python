"""재연결 판정 — 롤링 배포가 무중단이 되느냐 장애가 되느냐가 여기서 갈린다.

control 연결은 번호당 하나뿐이다. 서버가 그 자리를 새 프로세스에 넘겼는데 교체된 쪽이
재연결하면, 자기 연결을 되찾는 게 아니라 **방금 인계받은 프로세스를 밀어낸다**. 그러면
그쪽이 다시 재연결해 이쪽을 밀어내고, 겹치는 창 내내 자리를 주고받다가 서버의 reconnect
throttle 이 번호를 분 단위로 격리한다. 그래서 "재연결했는가" 는 실제 서버가 실제 소켓을
닫는 것에 대고 고정해야 한다.

이 파일은 이 스위트에서 처음으로 진짜 WebSocket 서버를 띄운다 — 그전까지는 URL 조립만
검증했고, 그래서 재연결 동작이 검증된 적이 없었다.
"""
from __future__ import annotations

import asyncio

import pytest
from aiohttp import web

from clawops.agent._control_ws import (
    CLOSE_OWNERSHIP_LOST,
    CLOSE_REPLACED,
    ControlWebSocket,
)


class _Server:
    def __init__(self) -> None:
        self.accepted: list[web.WebSocketResponse] = []
        self._runner: web.AppRunner | None = None
        self.port = 0

    async def start(self) -> None:
        app = web.Application()

        async def handler(request: web.Request) -> web.WebSocketResponse:
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            self.accepted.append(ws)
            async for _ in ws:
                pass
            return ws

        app.router.add_get("/v1/accounts/{account_id}/agent/listen", handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = self._runner.addresses[0][1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def stop(self) -> None:
        for ws in self.accepted:
            if not ws.closed:
                await ws.close()
        if self._runner:
            await self._runner.cleanup()


async def _client(server: _Server, seen: list[tuple[int, str]] | None = None) -> ControlWebSocket:
    async def _noop(_data: dict) -> None:
        return None

    return ControlWebSocket(
        base_url=server.base_url,
        api_key="key",
        account_id="AC1",
        number="07012345678",
        on_call_incoming=_noop,
        on_call_ended=_noop,
        on_terminal_close=(lambda code, reason: seen.append((code, reason))) if seen is not None else None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [CLOSE_REPLACED, CLOSE_OWNERSHIP_LOST])
async def test_does_not_reconnect_after_terminal_close(code: int) -> None:
    server = _Server()
    await server.start()
    seen: list[tuple[int, str]] = []
    ws = await _client(server, seen)
    task = asyncio.create_task(ws.connect())
    try:
        await ws.wait_connected(timeout=5)
        assert len(server.accepted) == 1

        await server.accepted[0].close(code=code, message=b"handover")
        # 재연결 backoff 는 1초에서 시작한다 — 일어날 거라면 이 창 안에 일어난다.
        await asyncio.sleep(1.8)

        assert len(server.accepted) == 1, "인계 통지를 받고도 재연결했다 — 새 프로세스를 밀어낸다"
        # 사유까지 고정한다. aiohttp 의 `async for` 는 CLOSE 를 몸통에 전달하지 않아
        # 사유가 조용히 빈 문자열이 되는데, 코드만 보면 통과해 그 구멍이 안 보인다.
        assert seen == [(code, "handover")]
    finally:
        await ws.close()
        task.cancel()
        await server.stop()


@pytest.mark.asyncio
async def test_still_reconnects_when_gateway_drains() -> None:
    """1001 은 자리를 뺏긴 게 아니라 그 게이트웨이가 빠지는 것 — 다시 붙어야 한다."""
    server = _Server()
    await server.start()
    seen: list[tuple[int, str]] = []
    ws = await _client(server, seen)
    task = asyncio.create_task(ws.connect())
    try:
        await ws.wait_connected(timeout=5)
        await server.accepted[0].close(code=1001, message=b"gateway draining")
        await asyncio.sleep(1.8)

        assert len(server.accepted) >= 2
        assert seen == []
    finally:
        await ws.close()
        task.cancel()
        await server.stop()
