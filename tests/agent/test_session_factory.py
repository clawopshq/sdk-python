"""통화당 Session 격리 (`session_factory=`).

왜 있나: ClawOpsAgent 한 인스턴스가 모든 동시 통화에 **같은 Session 객체**를 썼다
  (`_agent.py` 의 `session = self._session`). PipelineSession 은 self._call · self._messages ·
  self._audio_queue · self._tasks 를 들고 있어서, 두 번째 통화의 attach() 가 첫 통화의 대화
  이력과 전송 대상을 통째로 덮어쓰고, 첫 통화의 stop() 이 두 번째 통화의 task 를 내렸다.
  프로덕션 90일간 SDK 회선 7개에서 동시 인바운드 113건이 이 상태로 처리됐다.

고정하는 불변식:
  ① 팩토리 모드는 통화마다 다른 인스턴스를 준다
  ② 공유 모드는 오늘과 완전히 같이 동작한다 (하위호환 — 이걸 깨면 기존 회선이 죽는다)
  ③ 한 통화의 정리가 다른 통화의 세션을 건드리지 않는다
  ④ session / session_factory 는 정확히 하나
  ⑤ 프로세스 훅(session_setup/teardown)이 팩토리 모드에선 통화별로 돈다
"""
import asyncio
import logging

import pytest

from clawops._exceptions import AgentError
from clawops.agent._agent import ClawOpsAgent


class FakeSession:
    """Session Protocol 대역. 어떤 통화가 자기를 썼는지 기록한다."""

    instances: list["FakeSession"] = []

    def __init__(self, *, has_hooks: bool = False) -> None:
        self.stopped = 0
        self.prewarmed = 0
        self.setup_calls = 0
        self.teardown_calls = 0
        self.tools_frozen_after_prewarm = False
        if has_hooks:
            self.session_setup = self._session_setup  # type: ignore[method-assign]
            self.session_teardown = self._session_teardown  # type: ignore[method-assign]
        FakeSession.instances.append(self)

    async def _session_setup(self) -> None:
        self.setup_calls += 1

    async def _session_teardown(self) -> None:
        self.teardown_calls += 1

    async def start(self, call): ...
    async def prewarm(self) -> None:
        self.prewarmed += 1

    async def attach(self, call): ...
    async def feed_audio(self, audio: bytes, timestamp: int) -> None: ...
    async def feed_dtmf(self, digits: str) -> None: ...
    async def stop(self) -> None:
        self.stopped += 1

    def get_telemetry(self): return None


@pytest.fixture(autouse=True)
def _reset_instances():
    FakeSession.instances = []
    yield
    FakeSession.instances = []


def make_agent(*, factory: bool, has_hooks: bool = False) -> ClawOpsAgent:
    kwargs = (
        {"session_factory": lambda: FakeSession(has_hooks=has_hooks)}
        if factory
        else {"session": FakeSession(has_hooks=has_hooks)}
    )
    return ClawOpsAgent(
        api_key="test_key", account_id="AC_test", from_="07012345678", **kwargs
    )


# ── ④ 인자 배타 ────────────────────────────────────────────────


def test_both_session_and_factory_is_an_error():
    with pytest.raises(AgentError, match="하나만"):
        ClawOpsAgent(
            api_key="k",
            account_id="AC",
            from_="070",
            session=FakeSession(),
            session_factory=FakeSession,
        )


def test_neither_session_nor_factory_is_an_error():
    with pytest.raises(AgentError, match="session_factory"):
        ClawOpsAgent(api_key="k", account_id="AC", from_="070")


# ── ① 격리 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_factory_gives_each_call_its_own_session():
    agent = make_agent(factory=True)

    a = await agent._open_session("CA_a")
    b = await agent._open_session("CA_b")

    assert a is not b, "동시 통화가 같은 Session 을 공유하면 대화 이력이 서로를 덮어쓴다"
    assert agent._session_for("CA_a") is a
    assert agent._session_for("CA_b") is b


@pytest.mark.asyncio
async def test_repeated_open_for_the_same_call_reuses_one_session():
    """prewarm 과 통화 시작이 각각 _open_session 을 부른다 — 두 번째가 새로 만들면 안 된다."""
    agent = make_agent(factory=True)

    first = await agent._open_session("CA_x")
    second = await agent._open_session("CA_x")

    assert first is second
    assert len(FakeSession.instances) == 1


# ── ② 하위호환 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shared_mode_keeps_handing_out_the_same_object():
    agent = make_agent(factory=False)

    a = await agent._open_session("CA_a")
    b = await agent._open_session("CA_b")

    assert a is b is agent._session


@pytest.mark.asyncio
async def test_shared_mode_warns_on_a_second_concurrent_call(caplog):
    """오늘의 조용한 오염을 진단 가능한 것으로 바꾼다."""
    agent = make_agent(factory=False)

    with caplog.at_level(logging.ERROR, logger="clawops.agent"):
        await agent._open_session("CA_a")
        await agent._open_session("CA_b")

    assert any("session_factory" in r.getMessage() for r in caplog.records), (
        "두 번째 동시 통화에서 원인을 지목하는 로그가 나와야 한다"
    )


@pytest.mark.asyncio
async def test_shared_mode_does_not_warn_for_sequential_calls(caplog):
    agent = make_agent(factory=False)

    with caplog.at_level(logging.ERROR, logger="clawops.agent"):
        await agent._open_session("CA_a")
        await agent._close_session("CA_a")
        await agent._open_session("CA_b")

    assert caplog.records == [], "순차 통화는 정상이다 — 여기서 울면 경보가 무의미해진다"


# ── ③ 한 통화의 정리가 다른 통화를 건드리지 않는다 ──────────────


@pytest.mark.asyncio
async def test_prewarm_cleanup_stops_only_its_own_session():
    """실패한 발신의 정리가 진행 중인 다른 통화의 세션을 내리던 것."""
    agent = make_agent(factory=True)
    live = await agent._open_session("CA_live")
    doomed = await agent._open_session("CA_doomed")

    # CA_doomed 는 prewarm 만 하고 attach 되지 못한 상태다.
    agent._prewarm_tasks["CA_doomed"] = asyncio.create_task(asyncio.sleep(0))
    await asyncio.sleep(0)

    await agent._cleanup_prewarm("CA_doomed")

    assert doomed.stopped == 1
    assert live.stopped == 0, "진행 중이던 통화의 세션을 내리면 그 통화가 무음이 된다"


@pytest.mark.asyncio
async def test_closing_one_call_leaves_the_other_registered():
    agent = make_agent(factory=True)
    await agent._open_session("CA_a")
    b = await agent._open_session("CA_b")

    await agent._close_session("CA_a")

    assert "CA_a" not in agent._call_sessions
    assert agent._session_for("CA_b") is b


@pytest.mark.asyncio
async def test_session_for_does_not_resurrect_a_closed_call():
    """정리 코드가 세션을 되살리면 stop() 이 갓 만든 객체에 걸려 아무 일도 안 한다."""
    agent = make_agent(factory=True)
    await agent._open_session("CA_gone")
    await agent._close_session("CA_gone")

    with pytest.raises(AgentError):
        agent._session_for("CA_gone")
    assert len(FakeSession.instances) == 1


# ── ⑤ 프로세스 훅의 통화판 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_factory_mode_runs_hooks_per_call():
    agent = make_agent(factory=True, has_hooks=True)

    a = await agent._open_session("CA_a")
    b = await agent._open_session("CA_b")
    assert (a.setup_calls, b.setup_calls) == (1, 1)

    await agent._close_session("CA_a")
    assert (a.teardown_calls, b.teardown_calls) == (1, 0)


@pytest.mark.asyncio
async def test_shared_mode_hooks_stay_on_the_process_lifecycle():
    """공유 모드는 connect()/disconnect() 가 훅을 돌린다 — 통화마다 돌면 안 된다."""
    agent = make_agent(factory=False, has_hooks=True)

    await agent._open_session("CA_a")
    await agent._close_session("CA_a")

    assert agent._session.setup_calls == 0
    assert agent._session.teardown_calls == 0


@pytest.mark.asyncio
async def test_disconnect_tears_down_leftover_call_sessions():
    agent = make_agent(factory=True, has_hooks=True)
    a = await agent._open_session("CA_a")

    await agent.disconnect()

    assert a.teardown_calls == 1
    assert agent._call_sessions == {}
