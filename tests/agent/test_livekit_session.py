"""LiveKitSession — ClawOps Session Protocol 구현 테스트.

핵심은 두 가지:
- ClawOpsAgent 가 기대하는 Session 계약(start/prewarm/attach/feed_*/stop)을 지키는가
- 유저의 LiveKit 코드(Agent 서브클래스, @function_tool, AgentSession 설정)가
  손대지 않아도 그대로 도는가
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("livekit.agents", reason="clawops[livekit] 미설치")

from livekit.agents import Agent, AgentSession  # noqa: E402
from livekit.agents.llm import function_tool  # noqa: E402

from clawops.agent._builtin_tools import BuiltinTool  # noqa: E402
from clawops.agent._session import CallSession  # noqa: E402
from clawops.agent._tool import ToolRegistry  # noqa: E402
from clawops.agent.pipeline._base import Session  # noqa: E402
from clawops.agent.pipeline._buffering_call import _BufferingCall  # noqa: E402
from clawops.agent.livekit import LiveKitSession  # noqa: E402


def _make_call() -> CallSession:
    call = CallSession(
        call_id="test-call",
        from_number="01012345678",
        to_number="07012341234",
        account_id="AC123",
    )
    call._send_audio_fn = AsyncMock()
    call._send_clear_fn = AsyncMock()
    call._hangup_fn = AsyncMock()
    call._media_ws = None
    return call


def _bare_session() -> AgentSession:
    return AgentSession(stt=None, llm=None, tts=None, vad=None, turn_detection=None)


def _create_fn(agent: Agent | None = None):
    async def create(call):
        return _bare_session(), agent or Agent(instructions="test")

    return create


# ── Session Protocol 준수 ───────────────────────────────────────


def test_satisfies_session_protocol() -> None:
    """ClawOpsAgent 는 이 Protocol 로만 세션을 만진다."""
    assert isinstance(LiveKitSession(_create_fn()), Session)


async def test_start_boots_without_room() -> None:
    call = _make_call()
    lk = LiveKitSession(_create_fn())

    await lk.start(call)

    assert lk._session is not None
    assert lk._target is call
    await lk.stop()


async def test_feed_audio_reaches_livekit_input() -> None:
    call = _make_call()
    lk = LiveKitSession(_create_fn())
    await lk.start(call)

    await lk.feed_audio(b"\xff" * 160, 0)

    frame = await asyncio.wait_for(lk._input.__anext__(), timeout=1.0)
    assert frame.sample_rate == 8000
    assert frame.samples_per_channel == 160
    await lk.stop()


async def test_feed_audio_before_boot_is_noop() -> None:
    """control WS 이벤트 순서가 어긋나도 죽지 않아야 한다."""
    lk = LiveKitSession(_create_fn())
    await lk.feed_audio(b"\xff" * 160, 0)  # 예외가 나지 않으면 통과


async def test_stop_is_idempotent() -> None:
    lk = LiveKitSession(_create_fn())
    await lk.start(_make_call())
    await lk.stop()
    await lk.stop()


# ── prewarm / attach ────────────────────────────────────────────


async def test_prewarm_buffers_then_attach_drains() -> None:
    """prewarm 중 나온 오디오는 버퍼에 쌓였다가 attach 때 실제 통화로 흘러야 한다."""
    lk = LiveKitSession(_create_fn())
    await lk.prewarm()

    assert isinstance(lk._target, _BufferingCall)
    # prewarm 동안 그리팅 오디오가 나온 것처럼 버퍼에 직접 넣는다.
    await lk._target.send_audio(b"\x01" * 160)
    await lk._target.send_audio(b"\x02" * 160)

    call = _make_call()
    await lk.attach(call)

    sent = [c.args[0] for c in call._send_audio_fn.await_args_list]
    assert sent == [b"\x01" * 160, b"\x02" * 160]
    assert lk._target is call
    await lk.stop()


async def test_attach_without_prewarm_boots(caplog: pytest.LogCaptureFixture) -> None:
    """prewarm 이 실패해 attach 가 먼저 와도 통화가 살아야 한다."""
    lk = LiveKitSession(_create_fn())
    call = _make_call()

    await lk.attach(call)

    assert lk._session is not None
    assert lk._target is call
    await lk.stop()


async def test_attach_repoints_toolset_to_real_call() -> None:
    lk = LiveKitSession(_create_fn())
    await lk.prewarm()
    # prewarm 중에는 도구를 조립하지 않는다 — attach 가 유일한 조립 지점이다.
    assert lk._toolset is None

    call = _make_call()
    await lk.attach(call)

    assert lk._toolset is not None
    assert lk._toolset._call is call
    await lk.stop()


# ── 유저의 LiveKit 코드가 그대로 도는가 ─────────────────────────


async def test_user_livekit_tools_survive_builtin_injection() -> None:
    """유저의 @function_tool 이 우리 내장 도구 주입 뒤에도 남아 있어야 한다.

    update_tools 는 리스트를 '교체'하므로 병합을 틀리면 유저 도구가 사라진다.
    """

    @function_tool
    async def check_reservation(name: str) -> str:
        """look up a reservation"""
        return "ok"

    agent = Agent(instructions="test", tools=[check_reservation])
    lk = LiveKitSession(_create_fn(agent))

    await lk.start(_make_call())

    ids = [t.id for t in agent.tools]
    assert "check_reservation" in ids, "유저 도구가 사라졌다"
    assert "clawops_phone" in ids, "내장 전화 도구가 안 붙었다"
    await lk.stop()


async def test_agent_subclass_hooks_are_untouched() -> None:
    """유저의 Agent 서브클래스(on_enter 등)가 그대로 호출되어야 한다."""
    entered = asyncio.Event()

    class MyAgent(Agent):
        def __init__(self) -> None:
            super().__init__(instructions="test")

        async def on_enter(self) -> None:
            entered.set()

    lk = LiveKitSession(_create_fn(MyAgent()))
    await lk.start(_make_call())

    await asyncio.wait_for(entered.wait(), timeout=2.0)
    await lk.stop()


async def test_clawops_tool_registry_is_bridged() -> None:
    """기존 @agent.tool 로 등록한 도구도 LiveKit 쪽에 노출되어야 한다."""
    registry = ToolRegistry()

    async def lookup_order(order_id: str) -> str:
        """주문을 조회한다"""
        return "shipped"

    registry.register(lookup_order)

    agent = Agent(instructions="test")
    lk = LiveKitSession(_create_fn(agent))
    lk.set_tool_registry(registry)

    await lk.start(_make_call())

    assert "lookup_order" in [t.id for t in agent.tools]
    await lk.stop()


# ── transcript 훅 브리지 (기존 @agent.on("transcript") 앱 호환) ──


async def test_conversation_items_bridge_to_transcript_hook() -> None:
    """LiveKit 의 최종 대화 항목이 ClawOps `transcript` 훅으로 흘러야 한다.

    네이티브 세션은 call._emit("transcript", role, text) 를 부른다. 세션만 LiveKit 으로
    바꿔도 @agent.on("transcript") 로 트랜스크립트를 모으는 앱이 그대로 돌아야 한다.
    """
    from livekit.agents.llm import ChatMessage

    call = _make_call()
    got: list[tuple[str, str]] = []

    async def on_tx(c: object, role: str, text: str) -> None:
        got.append((role, text))

    call._event_handlers = {"transcript": [on_tx]}

    lk = LiveKitSession(_create_fn())
    await lk.start(call)

    lk._session.emit(  # type: ignore[union-attr]
        "conversation_item_added",
        type("E", (), {"item": ChatMessage(role="assistant", content=["안녕하세요"])})(),
    )
    lk._session.emit(  # type: ignore[union-attr]
        "conversation_item_added",
        type("E", (), {"item": ChatMessage(role="user", content=["영업시간이요"])})(),
    )
    # ChatMessage 가 아닌 항목(handoff 등)은 role 이 없어 무시돼야 한다.
    lk._session.emit(  # type: ignore[union-attr]
        "conversation_item_added", type("E", (), {"item": object()})()
    )
    await asyncio.sleep(0.05)

    assert got == [("assistant", "안녕하세요"), ("user", "영업시간이요")]
    await lk.stop()


async def test_empty_transcript_item_is_skipped() -> None:
    """빈 text_content 항목은 훅을 부르지 않는다 (빈 transcript 노이즈 방지)."""
    from livekit.agents.llm import ChatMessage

    call = _make_call()
    got: list[tuple[str, str]] = []

    async def on_tx(c: object, role: str, text: str) -> None:
        got.append((role, text))

    call._event_handlers = {"transcript": [on_tx]}

    lk = LiveKitSession(_create_fn())
    await lk.start(call)

    lk._session.emit(  # type: ignore[union-attr]
        "conversation_item_added",
        type("E", (), {"item": ChatMessage(role="assistant", content=[""])})(),
    )
    await asyncio.sleep(0.05)

    assert got == []
    await lk.stop()


# ── 내장 도구 on/off ────────────────────────────────────────────


async def test_builtin_tools_subset_is_honored() -> None:
    lk = LiveKitSession(_create_fn())
    lk.set_builtin_tools({BuiltinTool.HANG_UP})

    await lk.start(_make_call())

    names = [t.id for t in lk._toolset.tools]
    assert names == ["hang_up"]
    await lk.stop()


async def test_builtin_tools_none_yields_empty_toolset() -> None:
    lk = LiveKitSession(_create_fn())
    lk.set_builtin_tools(set())

    await lk.start(_make_call())

    assert list(lk._toolset.tools) == []
    await lk.stop()


# ── 회귀: prewarm 도구 베이킹 / 이름 충돌 ───────────────────────


async def test_attach_rebuilds_tools_set_after_prewarm() -> None:
    """아웃바운드 prewarm 은 setter 실행 전에 _boot(None) 한다.

    _boot 시엔 registry/builtin 이 비어 있어 도구가 비지만, setter 실행 후
    attach() 가 도구를 다시 붙여야 한다. 안 그러면 통화 내내 도구가 사라진다.
    """
    registry = ToolRegistry()

    async def lookup(order_id: str) -> str:
        """조회"""
        return "ok"

    registry.register(lookup)

    agent = Agent(instructions="test")
    lk = LiveKitSession(_create_fn(agent))

    # prewarm: setter 아직 실행 전
    await lk.prewarm()
    assert "lookup" not in [t.id for t in agent.tools], "prewarm 시엔 registry 가 비어 있어야"

    # 이제 ClawOpsAgent 가 setter 를 실행하고 attach
    lk.set_tool_registry(registry)
    lk.set_builtin_tools({BuiltinTool.HANG_UP})
    await lk.attach(_make_call())

    ids = [t.id for t in agent.tools]
    assert "lookup" in ids, "attach 후 registry 도구가 붙어야 한다"
    names = [t.id for t in lk._toolset.tools]
    assert names == ["hang_up"], "attach 후 builtin_tools 제한이 반영돼야 한다"
    await lk.stop()


async def test_user_tool_name_collision_with_builtin_is_deduped() -> None:
    """유저 도구가 내장 도구와 같은 이름이면 내장 쪽을 빼서 세션 시작 실패를 막는다.

    LiveKit ToolContext.flatten() 은 이름이 겹치면 ValueError 를 던진다.
    """
    from livekit.agents.llm import ToolContext

    @function_tool
    async def hang_up(reason: str) -> str:
        """유저 자신의 hang_up"""
        return reason

    agent = Agent(instructions="test", tools=[hang_up])
    lk = LiveKitSession(_create_fn(agent))
    lk.set_builtin_tools({BuiltinTool.HANG_UP, BuiltinTool.SEND_DTMF})

    await lk.start(_make_call())

    # 내장 hang_up 은 제외되고, send_dtmf 만 남아야 한다
    assert [t.id for t in lk._toolset.tools] == ["send_dtmf"]
    # flatten 이 duplicate 없이 성공해야 한다
    names = [
        getattr(t, "name", getattr(getattr(t, "info", None), "name", None))
        for t in ToolContext(agent.tools).flatten()
    ]
    assert names.count("hang_up") == 1
    await lk.stop()


async def test_builtin_collision_inside_user_toolset_is_deduped() -> None:
    """유저가 Toolset 안에 hang_up 을 넣어도 내장 쪽을 빼야 한다.

    Toolset 의 .id 는 묶음 이름이라 멤버 이름을 못 잡는다 — get_fnc_tool_names 로
    풀지 않으면 flatten 이 duplicate function name 으로 통화를 떨군다.
    """
    from livekit.agents.llm import ToolContext, Toolset, function_tool as ft

    class UserPhone(Toolset):
        def __init__(self) -> None:
            super().__init__(
                id="user_phone",
                tools=[ft(self._hang_up, name="hang_up", description="유저 hang_up")],
            )

        async def _hang_up(self, ctx: object) -> str:
            return "bye"

    agent = Agent(instructions="test", tools=[UserPhone()])
    lk = LiveKitSession(_create_fn(agent))
    lk.set_builtin_tools({BuiltinTool.HANG_UP, BuiltinTool.SEND_DTMF})

    await lk.start(_make_call())

    # 내장 hang_up 은 제외되고 send_dtmf 만 남아야 한다
    assert [t.id for t in lk._toolset.tools] == ["send_dtmf"]
    # flatten 이 duplicate 없이 성공해야 한다 (이 줄이 예전엔 ValueError 로 터졌다)
    names = [
        getattr(t, "name", getattr(getattr(t, "info", None), "name", None))
        for t in ToolContext(agent.tools).flatten()
    ]
    assert names.count("hang_up") == 1
    await lk.stop()
