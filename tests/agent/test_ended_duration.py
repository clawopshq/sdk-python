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


def test_new_session_starts_with_none():
    call = CallSession(call_id="CA_y", from_number="010", to_number="070", account_id="AC")

    assert call.ended_duration is None
