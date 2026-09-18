"""전환이 연결되지 않았을 때 **발신자에게** 들려주고 끊을 문장 (`failure_message`).

왜 있나: `after_transfer="terminate"` 에서 대상이 안 받으면 발신자는 아무 말 없이 끊겼다.
  `whisper` 는 **전화를 받은 담당자**에게만 들리므로 이 경우를 덮지 못한다 — 듣는 사람이 다르다.

고정하는 불변식:
  ① 문장을 주면 payload 에 `failureMessage` 로 실린다
  ② 안 주면 **키가 붙지 않는다** — 기본 동작이 오늘과 같아야 한다(기존 사용자 영향 0)
  ③ 빈 문자열도 보내지 않는다 — 빈 문장을 합성하러 왕복시킬 이유가 없다
  ④ `whisper` 와 함께 쓸 수 있다 — 담당자에게도, 발신자에게도 각각 들려야 한다

node SDK 의 tests/agent/transfer-failure-announcement.test.ts 와 짝이다. 두 SDK 가 어긋나면
문서 한 벌이 두 곳에서 거짓이 되므로 payload 키 이름까지 같이 고정한다.
"""
import pytest

from clawops.agent._session import CallSession


def make_call() -> tuple[CallSession, list[dict]]:
    call = CallSession(
        call_id="CA_t", from_number="01040494897", to_number="07012341234", account_id="AC"
    )
    sent: list[dict] = []

    async def transfer_fn(params: dict) -> dict:
        sent.append(params)
        return {"status": "no-answer"}

    call._transfer_fn = transfer_fn
    return call, sent


@pytest.mark.asyncio
async def test_message_rides_on_the_payload():
    call, sent = make_call()

    await call.transfer("01012345678", failure_message="연결되지 않았습니다.")

    assert sent[0]["failureMessage"] == "연결되지 않았습니다."


@pytest.mark.asyncio
async def test_absent_message_adds_no_key():
    call, sent = make_call()

    await call.transfer("01012345678")

    assert "failureMessage" not in sent[0]
    assert "failureVoice" not in sent[0]


@pytest.mark.asyncio
async def test_blank_message_is_not_sent():
    call, sent = make_call()

    await call.transfer("01012345678", failure_message="")

    assert "failureMessage" not in sent[0]


@pytest.mark.asyncio
async def test_voice_rides_along():
    call, sent = make_call()

    await call.transfer(
        "01012345678",
        failure_message="연결되지 않았습니다.",
        failure_voice="cartesia:voice-uuid",
    )

    assert sent[0]["failureVoice"] == "cartesia:voice-uuid"


@pytest.mark.asyncio
async def test_coexists_with_whisper():
    """듣는 사람이 반대다 — 둘은 함께 설정할 수 있어야 한다."""
    call, sent = make_call()

    await call.transfer(
        "01012345678",
        mode="warm",
        whisper="VIP 고객입니다.",
        failure_message="연결되지 않았습니다.",
    )

    assert sent[0]["whisper"] == "VIP 고객입니다."
    assert sent[0]["failureMessage"] == "연결되지 않았습니다."
