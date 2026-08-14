"""서버가 확정한 통화 시간을 `CallSession.ended_duration` 으로 노출한다.

왜 있나: `call.ended` 는 duration 을 실어 보내는데 SDK 가 그 값을 **읽지 않았다**.
  그래서 통화 기록을 자체 시스템에 적재하는 개발자는 종료 이벤트 하나로 기록을 마칠 수 없고,
  `duration`(SDK 가 로컬 시계로 재는 경과 시간)을 대신 쓰거나 REST 를 다시 조회해야 했다.

  서버 쪽도 같이 고쳤다 — 정상 종료 경로가 duration 0 을 보내고 있었다(전환 경로만 실제 값).
  **배포 순서상 서버가 먼저 나가므로**, 구 서버(0 또는 필드 없음)와 섞이는 구간이 실제로 있다.

고정하는 불변식:
  ① 서버가 준 값이 ended_duration 에 담긴다
  ② 필드가 없으면 None 을 유지한다 — 없는 값을 로컬 계산으로 지어내지 않는다
  ③ duration(로컬 경과 시간)은 건드리지 않는다. 둘은 의미가 다르다
"""
import pytest

from clawops.agent._agent import ClawOpsAgent
from clawops.agent._session import CallSession


class DummySession:
    async def start(self, call): ...
    async def prewarm(self) -> None: ...
    async def attach(self, call): ...
    async def feed_audio(self, audio: bytes, timestamp: int) -> None: ...
    async def feed_dtmf(self, digits: str) -> None: ...
    async def stop(self) -> None: ...
    def get_telemetry(self): return None


def make_agent_with_call(call_id: str = "CA_x") -> tuple[ClawOpsAgent, CallSession]:
    agent = ClawOpsAgent(
        api_key="k", account_id="AC", from_="07012341234", session=DummySession()
    )
    call = CallSession(
        call_id=call_id, from_number="010", to_number="070", account_id="AC"
    )
    agent._active_sessions[call_id] = call
    return agent, call


@pytest.mark.asyncio
async def test_server_duration_lands_on_ended_duration():
    agent, call = make_agent_with_call()

    await agent._handle_ended({"callId": "CA_x", "status": "completed", "duration": 91})

    assert call.ended_duration == 91
    assert call.ended_status == "completed"


@pytest.mark.asyncio
async def test_missing_duration_stays_none():
    """구 서버 호환. 배포 순서상 실제로 겪는 구간이다."""
    agent, call = make_agent_with_call()

    await agent._handle_ended({"callId": "CA_x", "status": "completed"})

    assert call.ended_duration is None, "없는 값을 지어내면 어느 쪽인지 구분할 수 없다"


@pytest.mark.asyncio
async def test_zero_duration_is_kept_as_zero():
    """0 도 서버가 준 값이다 — 응답 전 종료는 실제로 0 이다."""
    agent, call = make_agent_with_call()

    await agent._handle_ended({"callId": "CA_x", "status": "canceled", "duration": 0})

    assert call.ended_duration == 0


@pytest.mark.asyncio
async def test_local_duration_is_untouched():
    """duration 은 통화 중에도 읽히는 로컬 경과 시간이라 의미가 다르다."""
    agent, call = make_agent_with_call()
    before = call.duration

    await agent._handle_ended({"callId": "CA_x", "status": "completed", "duration": 91})

    assert call.duration >= before
    assert call.duration != 91, "로컬 경과 시간이 서버 값으로 덮이면 안 된다"


@pytest.mark.asyncio
async def test_duration_lands_after_media_teardown_popped_the_session():
    """정상 종료의 표준 순서 — 서버는 미디어 WS 를 먼저 닫고 나중에 종료 프레임을 보낸다.

    그 시점에 세션은 이미 _active_sessions 에서 빠져 있다. 여기서 값을 버리면 성사된
    통화(=이 기능의 주 대상)의 ended_duration 은 영원히 None 이 된다.
    """
    agent, call = make_agent_with_call()

    # 미디어 정리 경로가 한 일: 종료 확정 + active 에서 제거.
    call._mark_ended()
    agent._active_sessions.pop("CA_x", None)
    agent._recent_sessions["CA_x"] = call

    await agent._handle_ended({"callId": "CA_x", "status": "completed", "duration": 91})

    assert call.ended_duration == 91
    assert call.ended_status == "completed"


def test_new_session_starts_with_none():
    call = CallSession(call_id="CA_y", from_number="010", to_number="070", account_id="AC")

    assert call.ended_duration is None


@pytest.mark.asyncio
async def test_grace_lets_call_end_read_the_server_value():
    """call_end 핸들러가 ended_duration 을 읽을 수 있어야 한다 — 인바운드의 유일한 통로다.

    서버는 미디어 WS 를 먼저 닫고 자원 정리 뒤에 종료 프레임을 보낸다. 안 기다리면
    그 핸들러 안에서는 영영 None 이라 기능이 사실상 쓸모없어진다.
    """
    import asyncio

    agent, call = make_agent_with_call()
    seen: list[int | None] = []

    async def on_end(c):
        seen.append(c.ended_duration)

    call.on("call_end", on_end)

    async def deliver_frame_late():
        await asyncio.sleep(0.05)
        await agent._handle_ended({"callId": "CA_x", "status": "completed", "duration": 91})

    asyncio.create_task(deliver_frame_late())
    started = asyncio.get_running_loop().time()
    await agent._await_server_terminal(call)
    elapsed = asyncio.get_running_loop().time() - started
    await call._emit("call_end")

    assert seen == [91], f"call_end 가 서버 값을 봐야 한다 — 실제 {seen}"
    # 프레임이 왔는데도 상한을 다 쓰면 모든 통화의 call_end 가 그만큼 늦어진다.
    assert elapsed < 0.5, f"프레임 도착 즉시 풀려야 한다 — {elapsed:.2f}초 걸렸다"


@pytest.mark.asyncio
async def test_grace_gives_up_when_the_frame_never_comes():
    """제어 연결이 죽어 프레임이 안 오면 상한만큼 기다렸다가 그냥 진행한다."""
    import asyncio
    import clawops.agent._agent as agent_mod

    agent, call = make_agent_with_call()
    original = agent_mod.TERMINAL_FRAME_GRACE_S
    agent_mod.TERMINAL_FRAME_GRACE_S = 0.05
    try:
        await asyncio.wait_for(agent._await_server_terminal(call), timeout=1.0)
    finally:
        agent_mod.TERMINAL_FRAME_GRACE_S = original

    assert call.ended_duration is None
    assert agent._terminal_waiters == {}, "대기 항목이 남으면 누수다"


@pytest.mark.asyncio
async def test_grace_returns_immediately_when_value_already_present():
    """프레임이 미디어 정리보다 먼저 온 순서 — 기다릴 이유가 없다."""
    import asyncio

    agent, call = make_agent_with_call()
    call.ended_duration = 42

    await asyncio.wait_for(agent._await_server_terminal(call), timeout=0.5)

    assert agent._terminal_waiters == {}


def test_media_teardown_actually_waits_for_the_terminal_frame():
    """배선 검사 — grace 를 만들어 놓고 정리 경로에서 안 부르면 아무 효과가 없다.

    직전 판에서 실제로 그랬다: grace 호출을 지워도 테스트가 전부 통과했다(테스트가
    _await_server_terminal 을 직접 불렀으니까). 호출부를 잃으면 기능이 통째로 죽는다.
    """
    import inspect
    import clawops.agent._agent as agent_mod

    src = inspect.getsource(agent_mod.ClawOpsAgent._start_call_session)
    flat = " ".join(src.split())

    assert "_await_server_terminal(call)" in flat, "미디어 정리 경로가 grace 를 부르지 않는다"
    # 순서까지 본다 — call_end 뒤에 기다리면 핸들러는 여전히 None 을 본다.
    assert flat.index("_await_server_terminal(call)") < flat.index('_emit("call_end")'), (
        "grace 는 call_end 발화 **전**이어야 한다"
    )
