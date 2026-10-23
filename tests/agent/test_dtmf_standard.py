"""키패드 입력을 표준(Twilio <Gather>·LiveKit GetDtmfTask·Pipecat DTMFAggregator) 방식에 맞춘 동작.

왜 있나: 2026-10-23 실통화(현대차 셔클 구성)에서 기본 구성의 결함 네 개가 나왔다.
  ① 자동 전달 대기 0.5초 < 사람 손 간격(0.33~0.94초) → 534 가 5·3·4 로 쪼개져 모델이 세 번 대답
  ② 자동 전달에 종료키가 없어 # 이 글자로 따로 들어감
  ③ collect_dtmf 가 수집 전에 자동 전달된 키까지 가져가 「1534」
  ④ collect_dtmf 대기가 안내 음성 재생 중에 흘러 누를 틈 없이 빈 값 → 통화 종료
"""
import asyncio
import json

import pytest

from clawops._exceptions import AgentError
from clawops.agent._agent import ClawOpsAgent
from clawops.agent._hold_audio import hold_audio_allowed
from clawops.agent._media_ws import MediaWebSocket
from clawops.agent._session import CallSession


class RecordingSession:
    """feed_dtmf 호출을 기록하는 pipeline Session 대역."""

    def __init__(self) -> None:
        self.fed: list[str] = []

    async def feed_dtmf(self, digits: str) -> None:
        self.fed.append(digits)


class FakePlayout:
    """media WS 대역 — release() 를 부를 때까지 안내가 재생 중인 것처럼 군다."""

    def __init__(self) -> None:
        self._played = asyncio.Event()
        self.calls = 0

    def release(self) -> None:
        self._played.set()

    async def wait_played_out(self, timeout: float) -> bool:
        self.calls += 1
        try:
            await asyncio.wait_for(self._played.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False


def make_agent(**kwargs) -> ClawOpsAgent:
    return ClawOpsAgent(
        api_key="test_key",
        account_id="AC_test",
        from_="07012345678",
        session=RecordingSession(),
        **kwargs,
    )


def attach_call(agent: ClawOpsAgent, session: RecordingSession, call_id: str = "CA_t") -> CallSession:
    call = CallSession(call_id=call_id, from_number="01000000000", to_number="07012345678", account_id="AC_test")
    agent._active_sessions[call_id] = call
    agent._call_sessions[call_id] = session
    return call


def new_call() -> CallSession:
    return CallSession(call_id="CA_t", from_number="010", to_number="070", account_id="AC")


# ── ① 기본 대기 2초 ──


def test_기본_대기는_2초다():
    agent = make_agent()
    assert agent._passive_dtmf_debounce_ms == 2000
    assert agent._passive_dtmf_finish_key == "#"
    assert agent._passive_dtmf_max_digits is None


@pytest.mark.asyncio
async def test_사람_속도로_누른_세_자리가_한_번에_간다():
    """0.8초 간격(실측 사람 속도)을 대기 시간 비율 그대로 줄여 재현한다: 간격 0.08 < 대기 0.2."""
    agent = make_agent(passive_dtmf_debounce_ms=200)
    s = RecordingSession()
    call = attach_call(agent, s)
    for d in "534":
        agent._on_dtmf_event(call, d)
        await asyncio.sleep(0.08)
    await asyncio.sleep(0.35)
    assert s.fed == ["534"]


# ── ② 종료키·자리 수 ──


@pytest.mark.asyncio
async def test_종료키는_즉시_확정하고_값에서_빠진다():
    agent = make_agent(passive_dtmf_debounce_ms=5000)
    s = RecordingSession()
    call = attach_call(agent, s)
    for d in "534#":
        agent._on_dtmf_event(call, d)
    await asyncio.sleep(0.05)  # 5초 대기와 무관하게 바로
    assert s.fed == ["534"]


@pytest.mark.asyncio
async def test_빈_버퍼에_종료키만_누르면_그_키를_넘긴다():
    """「#번을 누르세요」 메뉴가 깨지지 않아야 한다."""
    agent = make_agent(passive_dtmf_debounce_ms=5000)
    s = RecordingSession()
    call = attach_call(agent, s)
    agent._on_dtmf_event(call, "#")
    await asyncio.sleep(0.05)
    assert s.fed == ["#"]


@pytest.mark.asyncio
async def test_종료키를_끄면_샵도_보통_키다():
    agent = make_agent(passive_dtmf_debounce_ms=100, passive_dtmf_finish_key=None)
    s = RecordingSession()
    call = attach_call(agent, s)
    for d in "12#":
        agent._on_dtmf_event(call, d)
    await asyncio.sleep(0.25)
    assert s.fed == ["12#"]


@pytest.mark.asyncio
async def test_자리_수가_차면_즉시_확정한다():
    agent = make_agent(passive_dtmf_debounce_ms=5000, passive_dtmf_max_digits=4)
    s = RecordingSession()
    call = attach_call(agent, s)
    for d in "123456":
        agent._on_dtmf_event(call, d)
    await asyncio.sleep(0.05)
    # 넘친 두 자리는 다음 값으로 넘어간다(문서에 「자리 수가 항상 같을 때만」 이라고 적는 이유).
    assert s.fed == ["1234"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"passive_dtmf_finish_key": "A"},
        {"passive_dtmf_finish_key": "##"},
        {"passive_dtmf_finish_key": ""},
        {"passive_dtmf_max_digits": 0},
        {"passive_dtmf_debounce_ms": -1},
    ],
)
def test_잘못된_설정은_생성할_때_막는다(kwargs):
    with pytest.raises(AgentError):
        make_agent(**kwargs)


@pytest.mark.asyncio
async def test_자동_전달_로그에_값을_쓰지_않는다(caplog):
    import logging

    agent = make_agent(passive_dtmf_debounce_ms=5000)
    s = RecordingSession()
    call = attach_call(agent, s)
    with caplog.at_level(logging.INFO, logger="clawops.agent"):
        for d in "4111#":
            agent._on_dtmf_event(call, d)
        await asyncio.sleep(0.05)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "4111" not in logged
    assert "4자리" in logged


# ── ③ 수집 구간 안의 키만 ──


@pytest.mark.asyncio
async def test_자동_전달된_키는_다음_수집에_섞이지_않는다():
    """실통화 s6: 동의 1 → collect_dtmf 로 534 → 「1534」 였다."""
    agent = make_agent(passive_dtmf_debounce_ms=50)
    s = RecordingSession()
    call = attach_call(agent, s)

    agent._on_dtmf_event(call, "1")
    await asyncio.sleep(0.1)
    assert s.fed == ["1"]

    task = asyncio.create_task(call.collect_dtmf(max_digits=4, timeout=0.3))
    await asyncio.sleep(0.02)
    for d in "534#":
        agent._on_dtmf_event(call, d)
    assert await task == "534"
    # 수집 구간 안 키는 자동 전달로 새지 않는다.
    await asyncio.sleep(0.1)
    assert s.fed == ["1"]


@pytest.mark.asyncio
async def test_수집을_열_때_남은_키를_버린다():
    call = new_call()
    call._route_dtmf("9")  # 구간 밖에서 들어온 키(방어 대상)
    task = asyncio.create_task(call.collect_dtmf(max_digits=3, timeout=0.2))
    await asyncio.sleep(0.02)
    for d in "12":
        call._route_dtmf(d)
    assert await task == "12"


# ── ④ 대기는 안내 재생이 끝난 뒤부터 ──


@pytest.mark.asyncio
async def test_안내_재생_중엔_대기가_흐르지_않는다():
    """실통화 s5: 5초가 텍스트 생성 시점부터 흘러 안내가 끝나기 전에 빈 값이 됐다."""
    call = new_call()
    playout = FakePlayout()
    call._media_ws = playout

    task = asyncio.create_task(call.collect_dtmf(max_digits=3, timeout=0.1))
    await asyncio.sleep(0.4)  # timeout 의 4배가 지나도
    assert not task.done(), "안내가 재생 중인데 대기가 끝났다"

    playout.release()
    await asyncio.sleep(0.05)
    for d in "53":
        call._route_dtmf(d)
    assert await task == "53"
    assert playout.calls == 1


@pytest.mark.asyncio
async def test_재생이_끝난_뒤부터_자리_사이_대기를_센다():
    call = new_call()
    playout = FakePlayout()
    call._media_ws = playout
    loop = asyncio.get_running_loop()

    task = asyncio.create_task(call.collect_dtmf(max_digits=3, timeout=0.15))
    await asyncio.sleep(0.3)
    playout.release()
    released = loop.time()
    assert await task == ""
    assert loop.time() - released >= 0.14


@pytest.mark.asyncio
async def test_재생_중_첫_키를_받고_안내를_끊는다():
    agent = make_agent()
    s = RecordingSession()
    call = attach_call(agent, s)
    playout = FakePlayout()
    call._media_ws = playout
    cleared: list[bool] = []

    async def clear() -> None:
        cleared.append(True)

    call._send_clear_fn = clear

    task = asyncio.create_task(call.collect_dtmf(max_digits=3, timeout=0.2))
    await asyncio.sleep(0.05)
    agent._on_dtmf_event(call, "5")  # 안내가 아직 재생 중
    await asyncio.sleep(0.02)
    agent._on_dtmf_event(call, "3")
    agent._on_dtmf_event(call, "4")
    assert await task == "534"
    assert cleared, "재생 중 키에 안내를 끊지 않았다"
    assert s.fed == []


@pytest.mark.asyncio
async def test_재생_끝을_못_알아도_상한에서_센다():
    """mark 가 안 오면(구 서버) max_wait 까지만 기다리고 그때부터 센다 — 무한 대기 금지."""
    call = new_call()
    call._media_ws = FakePlayout()  # release 안 함
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    result = await call.collect_dtmf(max_digits=3, timeout=0.05, max_wait=0.2)
    assert result == ""
    assert loop.time() - t0 < 1.0


@pytest.mark.asyncio
async def test_재생_끝을_알_수단이_없으면_바로_센다():
    call = new_call()  # _media_ws 없음 = 종전 동작
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    assert await call.collect_dtmf(max_digits=3, timeout=0.1) == ""
    assert loop.time() - t0 < 0.5


@pytest.mark.asyncio
async def test_재생_끝_mark_를_보내기_전에_waiter_를_건다():
    """재생할 게 없으면 echo 가 곧장 온다. waiter 를 나중에 걸면 그 echo 를 놓쳐 상한까지 간다."""
    mws = MediaWebSocket(url="wss://example.invalid/media", on_audio=None, on_start=None, on_stop=None)

    class InstantEchoWs:
        closed = False

        async def send_str(self, raw: str) -> None:
            msg = json.loads(raw)
            if msg.get("event") == "mark":
                # 서버가 즉시 돌려준 echo 를 수신 루프가 처리한 것과 같다.
                evt = mws._mark_waiters.get(msg["mark"]["name"])
                assert evt is not None, "mark 를 보낸 뒤에야 waiter 를 걸었다"
                evt.set()

    mws._ws = InstantEchoWs()
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    assert await mws.wait_played_out(timeout=2.0) is True
    assert loop.time() - t0 < 0.5
    assert mws._mark_waiters == {}


# ── 대기음 ──


def test_키를_받는_도구에는_대기음을_틀지_않는다():
    assert hold_audio_allowed(["lookup_order"]) is True
    assert hold_audio_allowed(["collect_dtmf"]) is False
    assert hold_audio_allowed(["lookup_order", "collect_dtmf"]) is False
