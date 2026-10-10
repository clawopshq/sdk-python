# 수신 통화 선별 훅 @agent.on_incoming (clawops#1251) — 받기 전에 call.reject 로 거절한다.
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from clawops.agent import ClawOpsAgent, IncomingCall


class _Recorder:
    """control WS 대역 — 보낸 프레임을 모은다."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, msg: dict) -> None:
        self.sent.append(msg)


def _agent(*, factory: bool = False) -> tuple[ClawOpsAgent, _Recorder, MagicMock]:
    session = MagicMock()
    session.session_setup = AsyncMock()
    kwargs = {"session_factory": lambda: session} if factory else {"session": session}
    agent = ClawOpsAgent(api_key="sk_test", account_id="AC_test", from_="07012341234", **kwargs)
    agent._safe_start_call_session = AsyncMock()  # type: ignore[method-assign]
    ws = _Recorder()
    agent._control_ws = ws  # type: ignore[assignment]
    agent._incoming_hook_deadline_s = 0.2
    return agent, ws, session


def _incoming(call_id: str = "C1", **extra) -> dict:
    return {"event": "call.incoming", "callId": call_id, "from": "01062915351", "mediaUrl": "wss://m", **extra}


async def _settle(agent: ClawOpsAgent) -> None:
    """훅을 따로 돌리므로 그 task 가 끝날 때까지 기다린다."""
    for _ in range(100):
        await asyncio.sleep(0.01)
        if not agent._screening:
            await asyncio.sleep(0.01)
            return
    raise AssertionError("screening did not finish")


def _assert_accepted(agent: ClawOpsAgent, ws: _Recorder, call_id: str = "C1") -> None:
    assert ws.sent == [{"event": "call.accept", "callId": call_id}]
    assert call_id in agent._active_sessions
    assert call_id in agent._call_sessions
    agent._safe_start_call_session.assert_called_once()  # type: ignore[attr-defined]


def _assert_rejected(agent: ClawOpsAgent, ws: _Recorder, reason: str, session: MagicMock, call_id: str = "C1") -> None:
    assert ws.sent == [{"event": "call.reject", "callId": call_id, "reason": reason}]
    assert call_id not in agent._active_sessions
    assert call_id not in agent._call_sessions
    agent._safe_start_call_session.assert_not_called()  # type: ignore[attr-defined]
    session.session_setup.assert_not_called()


@pytest.mark.asyncio
async def test_no_hook_accepts_inline_like_before():
    agent, ws, _ = _agent()
    await agent._handle_incoming(_incoming())
    # 훅이 없으면 task 없이 그 자리에서 수락한다 — 오늘과 같은 경로.
    _assert_accepted(agent, ws)


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", [False, True], ids=["shared", "factory"])
async def test_reject_default_is_rejected(factory):
    agent, ws, session = _agent(factory=factory)
    seen: list[IncomingCall] = []

    @agent.on_incoming
    async def screen(call: IncomingCall):
        seen.append(call)
        await call.reject()

    await agent._handle_incoming(_incoming(callerIdRestricted=True))
    await _settle(agent)
    _assert_rejected(agent, ws, "rejected", session)
    call = seen[0]
    assert (call.call_id, call.from_number, call.to_number, call.caller_id_restricted) == (
        "C1",
        "01062915351",
        "07012341234",
        True,
    )


@pytest.mark.asyncio
async def test_reject_busy():
    agent, ws, session = _agent(factory=True)

    async def screen(call: IncomingCall):
        await call.reject(reason="busy")

    assert agent.on_incoming(screen) is screen  # 데코레이터 아닌 직접 등록
    await agent._handle_incoming(_incoming())
    await _settle(agent)
    _assert_rejected(agent, ws, "busy", session)


@pytest.mark.asyncio
async def test_sync_hook_can_reject():
    agent, ws, session = _agent()

    @agent.on_incoming
    def screen(call: IncomingCall):
        call.reject("busy")  # 동기 훅 — await 없이도 효과가 있다

    await agent._handle_incoming(_incoming())
    await _settle(agent)
    _assert_rejected(agent, ws, "busy", session)


@pytest.mark.asyncio
async def test_hook_returns_without_reject_accepts():
    agent, ws, _ = _agent()

    @agent.on_incoming
    async def screen(call: IncomingCall):
        return None

    await agent._handle_incoming(_incoming(callerIdRestricted=False))
    await _settle(agent)
    _assert_accepted(agent, ws)
    assert agent._active_sessions["C1"].caller_id_restricted is False


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [False, True], ids=["async", "sync"])
async def test_hook_raises_accepts_with_warning(sync, caplog):
    agent, ws, _ = _agent()

    if sync:

        def screen(call):
            raise RuntimeError("boom")

    else:

        async def screen(call):
            raise RuntimeError("boom")

    agent.on_incoming(screen)
    with caplog.at_level(logging.WARNING, logger="clawops.agent"):
        await agent._handle_incoming(_incoming())
        await _settle(agent)
    _assert_accepted(agent, ws)
    assert any("예외" in r.getMessage() and "boom" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_slow_hook_accepts_and_late_reject_is_noop(caplog):
    agent, ws, _ = _agent()
    late = asyncio.Event()

    @agent.on_incoming
    async def screen(call: IncomingCall):
        await asyncio.sleep(0.5)  # 시한 0.2초
        await call.reject()
        late.set()

    with caplog.at_level(logging.WARNING, logger="clawops.agent"):
        await agent._handle_incoming(_incoming())
        await _settle(agent)
        _assert_accepted(agent, ws)
        await asyncio.wait_for(late.wait(), 2)
    # 시한 뒤의 reject 는 아무것도 보내지 않는다.
    assert ws.sent == [{"event": "call.accept", "callId": "C1"}]
    msgs = [r.getMessage() for r in caplog.records]
    assert any("결정하지 않았습니다" in m for m in msgs)
    assert any("시한이 지나" in m for m in msgs)


@pytest.mark.asyncio
async def test_reject_is_sent_without_waiting_for_hook_to_finish():
    agent, ws, session = _agent()
    release = asyncio.Event()

    @agent.on_incoming
    async def screen(call: IncomingCall):
        await call.reject()
        await call.reject("busy")  # 두 번째는 무시 — 첫 결정 유지
        await release.wait()

    await agent._handle_incoming(_incoming())
    await _settle(agent)
    _assert_rejected(agent, ws, "rejected", session)
    release.set()
    await asyncio.sleep(0.01)
    assert len(ws.sent) == 1


@pytest.mark.asyncio
async def test_invalid_reason_raises_value_error_and_accepts():
    call = IncomingCall(call_id="C", from_number="010", to_number="070")
    with pytest.raises(ValueError):
        call.reject("declined")  # type: ignore[arg-type]
    assert call.rejected is False

    agent, ws, _ = _agent()

    @agent.on_incoming
    async def screen(c: IncomingCall):
        await c.reject("486")  # type: ignore[arg-type]

    await agent._handle_incoming(_incoming())
    await _settle(agent)
    _assert_accepted(agent, ws)


@pytest.mark.asyncio
async def test_hook_does_not_block_control_loop():
    """훅이 도는 동안에도 다른 통화의 call.incoming 은 바로 처리된다."""
    agent, ws, _ = _agent()
    agent._incoming_hook_deadline_s = 5.0
    release = asyncio.Event()

    @agent.on_incoming
    async def screen(call: IncomingCall):
        if call.call_id == "SLOW":
            await release.wait()

    await asyncio.wait_for(agent._handle_incoming(_incoming("SLOW")), 0.1)
    await asyncio.wait_for(agent._handle_incoming(_incoming("FAST")), 0.1)
    await asyncio.sleep(0.05)
    assert ws.sent == [{"event": "call.accept", "callId": "FAST"}]
    release.set()
    await _settle(agent)
    assert {m["callId"] for m in ws.sent} == {"FAST", "SLOW"}


@pytest.mark.asyncio
async def test_caller_hangs_up_during_screening_no_accept():
    agent, ws, session = _agent(factory=True)
    agent._incoming_hook_deadline_s = 5.0
    release = asyncio.Event()

    @agent.on_incoming
    async def screen(call: IncomingCall):
        await release.wait()

    await agent._handle_incoming(_incoming())
    await asyncio.sleep(0.01)
    await agent._handle_ended({"event": "call.ended", "callId": "C1", "status": "canceled"})
    release.set()
    await _settle(agent)
    assert ws.sent == []
    assert "C1" not in agent._active_sessions
    session.session_setup.assert_not_called()


def test_on_incoming_rejects_non_callable():
    agent, _, _ = _agent()
    with pytest.raises(TypeError):
        agent.on_incoming("nope")  # type: ignore[arg-type]
