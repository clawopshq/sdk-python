"""핸드셰이크 거절(400·401·403)이면 재연결하지 않는다.

키가 폐기·만료됐거나 번호가 이 계정 것이 아니면 다시 붙어도 결과가 같다. 예전에는 이것도
1→30초 백오프로 영원히 재시도했다 — 2026-10-28 서버 실측: 폐기된 키·회수된 번호로 붙던 에이전트
21곳이 403 을 30초마다 끝없이 다시 보내고 있었고, 고객은 에이전트가 죽은 줄 몰랐다.

429·5xx 는 다르다 — 재연결 제한이나 게이트웨이 교체 중이라 자리가 실제로 비어 있을 수 있다.
"""
from __future__ import annotations

import asyncio

import pytest
from aiohttp import web

from clawops.agent import AgentAuthRejectedError
from clawops.agent._agent import ClawOpsAgent
from clawops.agent._control_ws import ControlWebSocket


class _RejectingServer:
    """control 경로에 정해 둔 상태로 거절한다. 시도 횟수를 센다."""

    def __init__(self, status: int, code: str | None) -> None:
        self.status = status
        self.code = code
        self.attempts = 0
        self._runner: web.AppRunner | None = None
        self.port = 0

    async def start(self) -> None:
        app = web.Application()

        async def handler(_request: web.Request) -> web.Response:
            self.attempts += 1
            headers = {"X-ClawOps-Error-Code": self.code} if self.code else {}
            body = f'{{"error":"{self.code}"}}' if self.code else ""
            return web.Response(status=self.status, headers=headers, text=body)

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
        if self._runner:
            await self._runner.cleanup()


def _client(server: _RejectingServer, rejected: list[AgentAuthRejectedError]) -> ControlWebSocket:
    async def _noop(_data: dict) -> None:
        return None

    return ControlWebSocket(
        base_url=server.base_url,
        api_key="key",
        account_id="AC1",
        number="07012345678",
        on_call_incoming=_noop,
        on_call_ended=_noop,
        on_rejected=rejected.append,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code", "needle"),
    [
        (403, "api_key_invalid", "API 키가 유효하지 않습니다"),
        (403, "number_not_owned", "번호 07012345678 는 계정 AC1 의 번호가 아닙니다"),
        (401, "api_key_missing", "API 키가 전달되지 않았습니다"),
        (400, "number_missing", "번호가 전달되지 않았습니다"),
        # 사유 헤더를 주지 않는 서버(구버전)여도 상태만으로 멈춘다.
        (403, None, "HTTP 403"),
    ],
)
async def test_rejected_handshake_stops_without_retry(status: int, code: str | None, needle: str) -> None:
    server = _RejectingServer(status, code)
    await server.start()
    rejected: list[AgentAuthRejectedError] = []
    ws = _client(server, rejected)
    task = asyncio.create_task(ws.connect())
    try:
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(AgentAuthRejectedError) as info:
            await ws.wait_connected(timeout=5)
        # 타임아웃을 기다리지 않고 거절 즉시 깨어나야 한다.
        assert loop.time() - started < 1.0
        assert info.value.status == status
        assert info.value.code == code
        assert needle in info.value.message

        # 재연결 backoff 는 1초에서 시작한다 — 일어날 거라면 이 창 안에 일어난다.
        await asyncio.sleep(1.8)
        assert server.attempts == 1, "거절당하고도 다시 붙었다 — 끝없이 반복한다"
        assert task.done(), "연결 태스크가 멈추지 않았다"
        assert rejected == [info.value]

        # 한 번 거절된 뒤의 대기도 곧바로 같은 오류를 준다.
        with pytest.raises(AgentAuthRejectedError):
            await ws.wait_connected(timeout=5)
    finally:
        task.cancel()
        await server.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_transient_handshake_failure_still_reconnects(status: int) -> None:
    """재연결 제한·게이트웨이 교체는 일시적이다 — 계속 붙어 본다."""
    server = _RejectingServer(status, None)
    await server.start()
    rejected: list[AgentAuthRejectedError] = []
    ws = _client(server, rejected)
    task = asyncio.create_task(ws.connect())
    try:
        with pytest.raises(asyncio.TimeoutError):
            await ws.wait_connected(timeout=1.8)
        assert server.attempts >= 2
        assert not task.done()
        assert rejected == []
    finally:
        await ws.close()
        task.cancel()
        await server.stop()


@pytest.mark.asyncio
async def test_agent_connect_raises_and_can_be_inspected() -> None:
    """에이전트의 connect() 가 곧바로 실패하고, 같은 객체로 다시 붙으려 해도 같은 오류다."""
    server = _RejectingServer(403, "api_key_invalid")
    await server.start()
    agent = ClawOpsAgent(
        api_key="sk_revoked",
        account_id="AC1",
        from_="07012345678",
        base_url=server.base_url,
        session=object(),  # type: ignore[arg-type]
    )
    try:
        with pytest.raises(AgentAuthRejectedError) as info:
            await agent.connect()
        assert info.value.code == "api_key_invalid"
        assert server.attempts == 1
        await asyncio.sleep(1.5)
        assert server.attempts == 1, "에이전트가 뒤에서 계속 붙고 있다"
    finally:
        await agent.disconnect()
        await server.stop()


@pytest.mark.asyncio
async def test_rejection_while_serving_ends_serve_with_the_error() -> None:
    """실행 중에 키가 폐기되면(재연결이 거절) serve() 가 정상 종료로 묻지 않고 오류로 끝난다."""
    agent = ClawOpsAgent(
        api_key="sk_test",
        account_id="AC1",
        from_="07012345678",
        session=object(),  # type: ignore[arg-type]
    )
    drained: list[dict] = []

    async def _connect() -> None:
        return None

    async def _drain(*, timeout: float, release_slot: bool = True) -> tuple[int, int]:
        drained.append({"release_slot": release_slot})
        return (0, 0)

    agent.connect = _connect  # type: ignore[method-assign]
    agent.drain = _drain  # type: ignore[method-assign]

    err = AgentAuthRejectedError("revoked", status=403, code="api_key_invalid")

    async def _reject_soon() -> None:
        await asyncio.sleep(0.05)
        agent._handle_rejected(err)

    asyncio.create_task(_reject_soon())
    with pytest.raises(AgentAuthRejectedError) as info:
        # 거절이면 인계를 기다리지 않는다 — 20초 상한에 걸리면 이 테스트가 시간 초과로 실패한다.
        await asyncio.wait_for(agent.serve(), timeout=5)
    assert info.value is err
    assert drained == [{"release_slot": True}]
    assert agent._ready is False
