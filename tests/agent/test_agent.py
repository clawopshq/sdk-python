# tests/agent/test_agent.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from clawops.agent import ClawOpsAgent
from clawops.agent.pipeline.realtime._openai import OpenAIRealtime


def _make_session(**kwargs):
    return OpenAIRealtime(api_key="sk-openai-test", **kwargs)


def test_agent_creation():
    agent = ClawOpsAgent(
        api_key="sk_test",
        account_id="AC_test",
        from_="07012341234",
        session=_make_session(system_prompt="test prompt"),
        rx_gain=0.8,
        tx_gain=1.2,
    )
    assert agent._from_number == "07012341234"
    assert agent._session._config.system_prompt == "test prompt"
    assert agent._rx_gain == 0.8
    assert agent._tx_gain == 1.2
    # machine_detection default 는 None (미지정 시 AMD 비활성)
    assert agent._machine_detection is None


def test_agent_machine_detection_default():
    agent = ClawOpsAgent(
        api_key="sk_test",
        account_id="AC_test",
        from_="07012341234",
        session=_make_session(),
        machine_detection="Hangup",
    )
    assert agent._machine_detection == "Hangup"


def test_agent_tool_decorator():
    agent = ClawOpsAgent(
        api_key="sk_test",
        account_id="AC_test",
        from_="07012341234",
        session=_make_session(),
    )

    @agent.tool
    async def greet(name: str) -> str:
        """인사합니다."""
        return f"안녕 {name}"

    assert "greet" in agent._tool_registry
    schemas = agent._tool_registry.to_openai_tools()
    assert len(schemas) == 1
    assert schemas[0]["name"] == "greet"


def test_agent_event_decorator():
    agent = ClawOpsAgent(
        api_key="sk_test",
        account_id="AC_test",
        from_="07012341234",
        session=_make_session(),
    )

    @agent.on("call_start")
    async def on_start(call):
        pass

    assert len(agent._event_handlers["call_start"]) == 1


def test_agent_from_env(monkeypatch):
    monkeypatch.setenv("CLAWOPS_API_KEY", "sk_env")
    monkeypatch.setenv("CLAWOPS_ACCOUNT_ID", "AC_env")

    agent = ClawOpsAgent(
        from_="07012341234",
        session=_make_session(),
    )
    assert agent._api_key == "sk_env"
    assert agent._account_id == "AC_env"


def test_agent_missing_api_key():
    from clawops._exceptions import AgentError
    with pytest.raises(AgentError, match="api_key"):
        ClawOpsAgent(
            from_="07012341234",
            session=_make_session(),
        )


def test_agent_missing_session():
    with pytest.raises(TypeError, match="session"):
        ClawOpsAgent(
            api_key="sk_test",
            account_id="AC_test",
            from_="07012341234",
        )


@pytest.mark.asyncio
async def test_outbound_ready_triggers_prewarm():
    """outbound_ready 이벤트 수신 시 session.prewarm() 이 백그라운드로 시작된다."""
    session_mock = MagicMock()
    session_mock.prewarm = AsyncMock()
    session_mock.attach = AsyncMock()
    session_mock.start = AsyncMock()
    session_mock.stop = AsyncMock()

    agent = ClawOpsAgent(
        api_key="sk_test",
        account_id="AC_test",
        from_="07012341234",
        session=session_mock,
    )

    # 미리 active_session 등록 (call() 메서드 우회)
    from clawops.agent._session import CallSession

    call = CallSession(
        call_id="C1",
        from_number="07012341234",
        to_number="07099998888",
        account_id="AC_test",
        direction="outbound",
    )
    agent._active_sessions["C1"] = call

    # _safe_start_call_session 은 실제 media WS 연결을 시도하므로 stub 으로 대체.
    agent._safe_start_call_session = AsyncMock()  # type: ignore[method-assign]

    await agent._handle_outbound_ready({"callId": "C1", "mediaUrl": "wss://media/C1"})
    # prewarm task 가 등록되어야 함
    assert "C1" in agent._prewarm_tasks
    # event loop 한 번 돌려 task 시작 보장
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    session_mock.prewarm.assert_awaited_once()

    # cleanup
    task = agent._prewarm_tasks.get("C1")
    if task and not task.done():
        task.cancel()


@pytest.mark.asyncio
async def test_ringing_triggers_prewarm():
    """call.ringing 수신 시 session.prewarm() 이 ring 구간에 미리 시작된다.

    prewarm 의 목적(answer 전 LLM 연결 + greeting 생성을 ring 시간으로 흡수)을
    달성하려면 outbound_ready(answer) 가 아니라 ringing 시점에 시작해야 한다.
    """
    session_mock = MagicMock()
    session_mock.prewarm = AsyncMock()
    session_mock.attach = AsyncMock()
    session_mock.start = AsyncMock()
    session_mock.stop = AsyncMock()

    agent = ClawOpsAgent(
        api_key="sk_test",
        account_id="AC_test",
        from_="07012341234",
        session=session_mock,
    )

    from clawops.agent._session import CallSession

    call = CallSession(
        call_id="CR1",
        from_number="07012341234",
        to_number="07099998888",
        account_id="AC_test",
        direction="outbound",
    )
    agent._active_sessions["CR1"] = call

    await agent._handle_ringing({"callId": "CR1"})
    # ring 시점에 prewarm task 가 등록되어야 함
    assert "CR1" in agent._prewarm_tasks
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    session_mock.prewarm.assert_awaited_once()

    # cleanup
    task = agent._prewarm_tasks.get("CR1")
    if task and not task.done():
        task.cancel()


@pytest.mark.asyncio
async def test_outbound_ready_does_not_double_prewarm_after_ringing():
    """ringing 으로 이미 prewarm 이 시작된 경우 outbound_ready 가 두 번째 prewarm 을 만들지 않는다."""
    session_mock = MagicMock()
    session_mock.prewarm = AsyncMock()
    session_mock.attach = AsyncMock()
    session_mock.start = AsyncMock()
    session_mock.stop = AsyncMock()

    agent = ClawOpsAgent(
        api_key="sk_test",
        account_id="AC_test",
        from_="07012341234",
        session=session_mock,
    )
    agent._safe_start_call_session = AsyncMock()  # type: ignore[method-assign]

    from clawops.agent._session import CallSession

    call = CallSession(
        call_id="CR2",
        from_number="07012341234",
        to_number="07099998888",
        account_id="AC_test",
        direction="outbound",
    )
    agent._active_sessions["CR2"] = call

    await agent._handle_ringing({"callId": "CR2"})
    first_task = agent._prewarm_tasks.get("CR2")
    await agent._handle_outbound_ready({"callId": "CR2", "mediaUrl": "wss://media/CR2"})
    # 동일 task 가 유지되어야 함 (재생성 X)
    assert agent._prewarm_tasks.get("CR2") is first_task
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    session_mock.prewarm.assert_awaited_once()

    # cleanup
    if first_task and not first_task.done():
        first_task.cancel()


def _mock_originate_ok():
    """aiohttp POST → 201 {"callId": "CO1"} 모킹 컨텍스트. (http_session, patch_target)."""
    resp = MagicMock()
    resp.status = 201
    resp.json = AsyncMock(return_value={"callId": "CO1"})
    post_ctx = MagicMock()
    post_ctx.__aenter__ = AsyncMock(return_value=resp)
    post_ctx.__aexit__ = AsyncMock(return_value=False)
    http_session = MagicMock()
    http_session.post = MagicMock(return_value=post_ctx)
    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=http_session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)
    return http_session, session_ctx


def _posted_body(http_session) -> dict:
    return http_session.post.call_args.kwargs["json"]


@pytest.mark.asyncio
async def test_call_starts_prewarm_at_originate():
    """agent.call() 이 originate(POST 201) 직후 prewarm 을 시작한다.

    ring 구간(answer 이전)을 활용하려면 ringing/outbound_ready 를 기다리지 않고
    발신 즉시 prewarm 해야 한다 (call.ringing 이 서버에서 안 올 수 있음).
    """
    session_mock = MagicMock()
    session_mock.prewarm = AsyncMock()
    session_mock.attach = AsyncMock()
    session_mock.start = AsyncMock()
    session_mock.stop = AsyncMock()

    agent = ClawOpsAgent(
        api_key="sk_test",
        account_id="AC_test",
        from_="07012341234",
        session=session_mock,
    )
    agent.connect = AsyncMock()  # type: ignore[method-assign]  # control WS 우회

    _http_session, session_ctx = _mock_originate_ok()
    with patch("aiohttp.ClientSession", return_value=session_ctx):
        call = await agent.call("07099998888")

    assert call.call_id == "CO1"
    # originate 직후 prewarm task 가 등록되어야 함
    assert "CO1" in agent._prewarm_tasks
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    session_mock.prewarm.assert_awaited_once()

    # cleanup
    task = agent._prewarm_tasks.get("CO1")
    if task and not task.done():
        task.cancel()


async def _originate_body(*, instance_md=None, call_md=None) -> dict:
    """agent 를 originate 하고 POST body 를 돌려준다.

    prewarm_enabled=False 로 prewarm task/정리를 회피한다. instance_md 는 생성자
    default, call_md 는 call() 인자로 전달돼 우선순위(call_md > instance_md)를 검증한다.
    """
    agent = ClawOpsAgent(
        api_key="sk_test", account_id="AC_test", from_="07012341234",
        session=MagicMock(), prewarm_enabled=False, machine_detection=instance_md,
    )
    agent.connect = AsyncMock()  # type: ignore[method-assign]
    http_session, session_ctx = _mock_originate_ok()
    with patch("aiohttp.ClientSession", return_value=session_ctx):
        await agent.call("07099998888", machine_detection=call_md)
    return _posted_body(http_session)


@pytest.mark.asyncio
async def test_call_omits_machine_detection_by_default():
    """machine_detection 미지정 시 MachineDetection 이 body 에 실리지 않는다."""
    assert "MachineDetection" not in await _originate_body()


@pytest.mark.asyncio
async def test_call_uses_instance_default_machine_detection():
    """인스턴스 default 가 body 에 반영된다."""
    assert (await _originate_body(instance_md="Hangup"))["MachineDetection"] == "Hangup"


@pytest.mark.asyncio
async def test_call_arg_overrides_instance_default():
    """호출 인자가 인스턴스 default 보다 우선한다 (호출 인자 > default)."""
    assert (await _originate_body(instance_md="Hangup", call_md="Enable"))["MachineDetection"] == "Enable"


@pytest.mark.asyncio
async def test_prewarm_disabled_skips_prewarm():
    """prewarm_enabled=False 면 outbound_ready 수신 시 prewarm task 가 등록되지 않는다."""
    session_mock = MagicMock()
    session_mock.prewarm = AsyncMock()

    agent = ClawOpsAgent(
        api_key="sk_test",
        account_id="AC_test",
        from_="07012341234",
        session=session_mock,
        prewarm_enabled=False,
    )
    agent._safe_start_call_session = AsyncMock()  # type: ignore[method-assign]

    from clawops.agent._session import CallSession

    call = CallSession(
        call_id="C3",
        from_number="07012341234",
        to_number="07099998888",
        account_id="AC_test",
        direction="outbound",
    )
    agent._active_sessions["C3"] = call
    await agent._handle_outbound_ready({"callId": "C3", "mediaUrl": "wss://media/C3"})
    assert "C3" not in agent._prewarm_tasks
    session_mock.prewarm.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_before_attach_stops_prewarmed_session():
    """attach 전에 call.failed 수신 시 prewarm 된 세션을 stop() 으로 닫는다 (LLM WS leak 방지)."""
    session_mock = MagicMock()
    session_mock.prewarm = AsyncMock()
    session_mock.stop = AsyncMock()

    agent = ClawOpsAgent(
        api_key="sk_test", account_id="AC_test", from_="07012341234", session=session_mock
    )

    async def _done():
        return None

    agent._prewarm_tasks["CF1"] = asyncio.create_task(_done())
    await asyncio.sleep(0)

    await agent._handle_failed({"callId": "CF1", "reason": "rejected"})

    session_mock.stop.assert_awaited_once()
    assert "CF1" not in agent._prewarm_tasks


@pytest.mark.asyncio
async def test_ended_after_attach_does_not_stop():
    """이미 attach 된 통화 종료 시 cleanup 이 stop() 을 중복 호출하지 않는다 (정상 경로가 책임)."""
    session_mock = MagicMock()
    session_mock.prewarm = AsyncMock()
    session_mock.stop = AsyncMock()

    agent = ClawOpsAgent(
        api_key="sk_test", account_id="AC_test", from_="07012341234", session=session_mock
    )

    async def _done():
        return None

    agent._prewarm_tasks["CE1"] = asyncio.create_task(_done())
    agent._prewarm_attached.add("CE1")
    await asyncio.sleep(0)

    await agent._handle_ended({"callId": "CE1"})

    session_mock.stop.assert_not_awaited()
    assert "CE1" not in agent._prewarm_tasks


@pytest.mark.asyncio
async def test_prewarm_failure_marks_call_failed():
    """prewarm 실패 시 _prewarm_failed 셋에 등록된다."""
    session_mock = MagicMock()
    session_mock.prewarm = AsyncMock(side_effect=RuntimeError("boom"))

    agent = ClawOpsAgent(
        api_key="sk_test",
        account_id="AC_test",
        from_="07012341234",
        session=session_mock,
    )

    await agent._prewarm_session("C2")
    assert "C2" in agent._prewarm_failed


def test_agent_rejects_invalid_gain():
    with pytest.raises(ValueError, match="rx_gain"):
        ClawOpsAgent(
            api_key="sk_test",
            account_id="AC_test",
            from_="07012341234",
            session=_make_session(),
            rx_gain=-0.1,
        )
    with pytest.raises(ValueError, match="tx_gain"):
        ClawOpsAgent(
            api_key="sk_test",
            account_id="AC_test",
            from_="07012341234",
            session=_make_session(),
            tx_gain=float("inf"),
        )
