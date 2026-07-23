"""prewarm 이 @agent.tool / MCP 도구를 누락하지 않는지 검증.

회귀 대상: 발신 통화는 originate 직후 prewarm 이 돌면서 LLM 에 tool 스키마를
확정 전송하는데, 도구 주입(_start_call_session)은 상대가 받은 뒤에야 실행돼
유저 도구가 통째로 빠진 채 통화가 시작됐다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clawops.agent import ClawOpsAgent
from clawops.agent._tool import ToolRegistry
from clawops.agent.pipeline.realtime._openai import OpenAIRealtime


def _make_mock_connection() -> MagicMock:
    conn = MagicMock()
    conn.session = MagicMock()
    conn.session.update = AsyncMock()
    conn.response = MagicMock()
    conn.response.create = AsyncMock()
    conn.input_audio_buffer = MagicMock()
    conn.input_audio_buffer.append = AsyncMock()
    conn.close = AsyncMock()

    async def _aiter():
        if False:
            yield None
        return

    conn.__aiter__ = lambda self_: _aiter()
    return conn


def _sent_tool_names(update_mock, call_index: int = 0) -> list[str]:
    session = update_mock.await_args_list[call_index].kwargs["session"]
    return [t.get("name") for t in session.get("tools", [])]


def _registry_with(name: str) -> ToolRegistry:
    reg = ToolRegistry()

    async def fn(query: str = "up") -> str:
        """테스트용 도구."""
        return "ok"

    fn.__name__ = name
    reg.register(fn)
    return reg


def _mock_call() -> MagicMock:
    call = MagicMock()
    call.send_audio = AsyncMock()
    call._emit = AsyncMock()
    call.metrics = MagicMock()
    return call


# ── 세션 레벨 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prewarm_sends_injected_tools() -> None:
    """prewarm 전에 주입된 도구는 첫 session.update 에 실린다."""
    sess = OpenAIRealtime(api_key="sk-test", greeting=False)
    sess.set_tool_registry(_registry_with("query_prometheus"))
    conn = _make_mock_connection()
    with patch.object(sess, "_open_connection", new=AsyncMock(return_value=conn)):
        await sess.prewarm()
    assert "query_prometheus" in _sent_tool_names(conn.session.update)
    await sess.stop()


@pytest.mark.asyncio
async def test_attach_resyncs_tools_added_after_prewarm() -> None:
    """prewarm 이후 붙은 도구(MCP)는 attach 에서 재전송된다."""
    sess = OpenAIRealtime(api_key="sk-test", greeting=False)
    conn = _make_mock_connection()
    with patch.object(sess, "_open_connection", new=AsyncMock(return_value=conn)):
        await sess.prewarm()
    assert "late_tool" not in _sent_tool_names(conn.session.update)

    sess.set_tool_registry(_registry_with("late_tool"))
    await sess.attach(_mock_call())

    assert conn.session.update.await_count == 2
    assert "late_tool" in _sent_tool_names(conn.session.update, 1)
    await sess.stop()


@pytest.mark.asyncio
async def test_attach_skips_resync_when_tools_unchanged() -> None:
    """도구가 그대로면 불필요한 session.update 를 보내지 않는다."""
    sess = OpenAIRealtime(api_key="sk-test", greeting=False)
    sess.set_tool_registry(_registry_with("query_prometheus"))
    conn = _make_mock_connection()
    with patch.object(sess, "_open_connection", new=AsyncMock(return_value=conn)):
        await sess.start(_mock_call())  # prewarm + attach
    assert conn.session.update.await_count == 1
    await sess.stop()


@pytest.mark.asyncio
async def test_builtin_tool_before_answer_returns_result_instead_of_crashing() -> None:
    """prewarm 창에서 hang_up 이 호출돼도 예외 대신 결과를 모델에 돌려준다.

    회귀 대상: _BufferingCall 에 hangup 이 없어 AttributeError 가 나고, tool 결과가
    영영 돌아가지 않아 모델이 응답을 멈춘 채로 통화가 시작됐다.
    """
    from clawops.agent.pipeline._builtin_tool_schemas import (
        CALL_NOT_READY_RESULT,
        execute_builtin_tool,
    )
    from clawops.agent.pipeline._buffering_call import _BufferingCall

    for name, args in [
        ("hang_up", {}),
        ("transfer_call", {"to": "07012341234"}),
        ("collect_dtmf", {}),
        ("send_dtmf", {"digits": "123"}),
    ]:
        result = await execute_builtin_tool(name, args, _BufferingCall())
        assert result == CALL_NOT_READY_RESULT


@pytest.mark.asyncio
async def test_openai_prewarm_tool_call_feeds_result_back() -> None:
    """prewarm 중 hang_up tool call → function_call_output 이 모델로 돌아간다."""
    sess = OpenAIRealtime(api_key="sk-test", greeting=False)
    conn = _make_mock_connection()
    conn.conversation = MagicMock()
    conn.conversation.item = MagicMock()
    conn.conversation.item.create = AsyncMock()
    with patch.object(sess, "_open_connection", new=AsyncMock(return_value=conn)):
        await sess.prewarm()

    item = MagicMock()
    item.name = "hang_up"
    item.call_id = "call_1"
    item.arguments = "{}"
    await sess._handle_tool_call(item)

    conn.conversation.item.create.assert_awaited_once()
    output = conn.conversation.item.create.await_args.kwargs["item"]["output"]
    assert "연결되지 않았습니다" in output
    await sess.stop()


# ── 에이전트 레벨 ────────────────────────────────────────────────────


def _make_agent(session, **kwargs) -> ClawOpsAgent:
    return ClawOpsAgent(
        api_key="sk-test",
        account_id="acct-test",
        from_="07012341234",
        session=session,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_agent_injects_tools_before_prewarm() -> None:
    """_prewarm_session 이 prewarm() 호출 전에 도구를 주입한다."""
    order: list[str] = []
    session = MagicMock()
    session.set_tool_registry = MagicMock(side_effect=lambda r: order.append("tools"))
    session.set_builtin_tools = MagicMock()
    session.prewarm = AsyncMock(side_effect=lambda: order.append("prewarm"))

    agent = _make_agent(session)

    @agent.tool
    async def query_prometheus(query: str = "up") -> str:
        """테스트용 도구."""
        return "ok"

    await agent._prewarm_session("call-1")

    assert order == ["tools", "prewarm"]
    injected = session.set_tool_registry.call_args[0][0]
    assert "query_prometheus" in [t["name"] for t in injected.to_openai_tools()]


@pytest.mark.asyncio
async def test_agent_skips_prewarm_for_frozen_session_with_mcp() -> None:
    """도구 고정 세션(Gemini) + MCP 조합이면 prewarm 을 건너뛴다."""
    session = MagicMock()
    session.tools_frozen_after_prewarm = True
    session.prewarm = AsyncMock()

    agent = _make_agent(session, mcp_servers=[MagicMock()])
    agent._start_prewarm("call-1")
    assert agent._prewarm_tasks == {}

    # MCP 가 없으면 정상적으로 prewarm 한다.
    agent2 = _make_agent(session)
    agent2._start_prewarm("call-2")
    assert "call-2" in agent2._prewarm_tasks
    await agent2._prewarm_tasks["call-2"]


@pytest.mark.asyncio
async def test_agent_start_prewarm_is_idempotent() -> None:
    session = MagicMock()
    session.prewarm = AsyncMock()
    agent = _make_agent(session)
    agent._start_prewarm("call-1")
    first = agent._prewarm_tasks["call-1"]
    agent._start_prewarm("call-1")
    assert agent._prewarm_tasks["call-1"] is first
    await first


@pytest.mark.asyncio
async def test_agent_respects_prewarm_disabled() -> None:
    session = MagicMock()
    session.prewarm = AsyncMock()
    agent = _make_agent(session, prewarm_enabled=False)
    agent._start_prewarm("call-1")
    assert agent._prewarm_tasks == {}
