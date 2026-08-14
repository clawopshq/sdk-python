"""동시 통화의 패시브 DTMF 가 서로 섞이지 않는지.

왜 있나: _passive_dtmf_buffer / _passive_dtmf_task / _passive_dtmf_call_id 가 통화가 아니라
  ClawOpsAgent 인스턴스에 붙어 있었다. 두 통화가 debounce 창(기본 500ms) 안에서 키를 누르면
  버퍼가 합쳐지고, flush 는 _passive_dtmf_call_id 가 가리키는 **마지막 통화 하나**에만 갔다 —
  한 통화는 입력을 통째로 잃고 다른 통화는 남의 숫자를 받는다.

고정하는 불변식:
  ① 통화마다 자기 버퍼로 debounce 한다
  ② flush 중인 주입은 다른 통화의 입력에 잘리지 않는다 (cancel → identity guard)
  ③ 종료된 통화의 뒤늦은 flush 는 아무 일도 하지 않는다
"""
import asyncio

import pytest

from clawops.agent._agent import ClawOpsAgent
from clawops.agent._session import CallSession


DEBOUNCE_MS = 60


class RecordingSession:
    """feed_dtmf 호출을 기록하는 pipeline Session 대역."""

    def __init__(self, *, feed_delay: float = 0.0) -> None:
        self.fed: list[str] = []
        self._feed_delay = feed_delay

    async def feed_dtmf(self, digits: str) -> None:
        if self._feed_delay:
            await asyncio.sleep(self._feed_delay)
        self.fed.append(digits)


def make_agent(**kwargs) -> ClawOpsAgent:
    return ClawOpsAgent(
        api_key="test_key",
        account_id="AC_test",
        from_="07012345678",
        session=RecordingSession(),
        passive_dtmf_debounce_ms=DEBOUNCE_MS,
        **kwargs,
    )


def attach_call(agent: ClawOpsAgent, call_id: str, session: RecordingSession) -> CallSession:
    call = CallSession(
        call_id=call_id, from_number="01000000000", to_number="07012345678", account_id="AC_test"
    )
    agent._active_sessions[call_id] = call
    agent._call_sessions[call_id] = session
    return call


async def settle(multiplier: float = 3.0) -> None:
    await asyncio.sleep(DEBOUNCE_MS / 1000 * multiplier)


@pytest.mark.asyncio
async def test_concurrent_calls_do_not_share_a_buffer():
    """두 통화가 같은 debounce 창에서 키를 눌러도 각자 자기 숫자만 받는다."""
    agent = make_agent()
    sa, sb = RecordingSession(), RecordingSession()
    a = attach_call(agent, "CA_a", sa)
    b = attach_call(agent, "CA_b", sb)

    agent._on_dtmf_event(a, "1")
    agent._on_dtmf_event(b, "9")
    agent._on_dtmf_event(a, "2")
    agent._on_dtmf_event(b, "8")

    await settle()

    assert sa.fed == ["12"], f"A 가 자기 입력만 받아야 한다 — 실제 {sa.fed}"
    assert sb.fed == ["98"], f"B 가 자기 입력만 받아야 한다 — 실제 {sb.fed}"


@pytest.mark.asyncio
async def test_single_call_still_debounces_into_one_injection():
    """기존 동작 회귀 — 한 통화의 연속 입력은 여전히 한 번에 합쳐진다."""
    agent = make_agent()
    s = RecordingSession()
    call = attach_call(agent, "CA_one", s)

    for digit in "123":
        agent._on_dtmf_event(call, digit)

    await settle()

    assert s.fed == ["123"]


@pytest.mark.asyncio
async def test_in_flight_injection_is_not_truncated_by_a_later_keypress():
    """주입이 진행 중일 때 같은 통화에서 키를 더 눌러도 그 주입이 잘리지 않는다.

    이전 구현은 digit 마다 앞선 flush task 를 cancel 했다. 그 task 가 이미 debounce sleep 을
    지나 feed_dtmf 안(LLM 주입 중)에 들어가 있으면 취소가 **주입 자체를 중간에 끊는다**.
    debounce 간격보다 느긋하게 누르는 발신자에게 그대로 나타난다 — 앞서 누른 숫자가 사라진다.

    지금은 cancel 하지 않고, 깨어난 flush 가 "내가 최신인가"를 확인해 스스로 물러난다.
    """
    agent = make_agent()
    s = RecordingSession(feed_delay=DEBOUNCE_MS / 1000 * 4)
    call = attach_call(agent, "CA_slow", s)

    agent._on_dtmf_event(call, "7")
    await settle(2.0)  # flush 가 feed_dtmf 안에서 대기 중

    agent._on_dtmf_event(call, "8")
    await settle(10.0)

    assert s.fed == ["7", "8"], f"진행 중이던 '7' 주입이 살아남아야 한다 — 실제 {s.fed}"


@pytest.mark.asyncio
async def test_debounce_anchors_on_the_last_keypress_not_the_first():
    """debounce 창을 넘겨 가며 천천히 누른 숫자도 한 번에 주입된다.

    flush 를 취소하지 않는 대신 "내가 최신인가" 검사로 물러나게 했는데, 그 검사가 없으면
    가장 먼저 뜬 flush 가 첫 키 + debounce 시점에 깨어나 버퍼를 먼저 비운다 — 그러면
    debounce 가 **마지막 키가 아니라 첫 키**에 걸려 "123" 이 "12" 와 "3" 으로 쪼개진다.
    """
    agent = make_agent()
    s = RecordingSession()
    call = attach_call(agent, "CA_slow_presser", s)

    agent._on_dtmf_event(call, "1")
    await settle(0.5)
    agent._on_dtmf_event(call, "2")
    await settle(0.5)
    agent._on_dtmf_event(call, "3")

    await settle(4.0)

    assert s.fed == ["123"], f"한 번에 주입돼야 한다 — 실제 {s.fed}"


@pytest.mark.asyncio
async def test_flush_after_call_end_is_a_noop():
    """통화가 끝난 뒤 깨어난 flush 는 아무것도 주입하지 않는다."""
    agent = make_agent()
    s = RecordingSession()
    call = attach_call(agent, "CA_end", s)

    agent._on_dtmf_event(call, "5")
    call._mark_ended("completed")

    await settle()

    assert s.fed == []
    assert call._passive_dtmf_task is None
    assert call._passive_dtmf_buffer == []


@pytest.mark.asyncio
async def test_collector_takes_precedence_per_call():
    """한 통화가 collect_dtmf 중이어도 다른 통화의 패시브 경로는 정상 동작한다."""
    agent = make_agent()
    sa, sb = RecordingSession(), RecordingSession()
    a = attach_call(agent, "CA_a", sa)
    b = attach_call(agent, "CA_b", sb)

    collecting = asyncio.create_task(a.collect_dtmf(max_digits=2, timeout=2))
    await asyncio.sleep(0.01)

    agent._on_dtmf_event(a, "4")
    agent._on_dtmf_event(a, "5")
    agent._on_dtmf_event(b, "6")

    assert await asyncio.wait_for(collecting, timeout=1.0) == "45"
    await settle()

    assert sa.fed == [], "collector 가 가져간 입력은 패시브로도 주입되면 안 된다"
    assert sb.fed == ["6"]
