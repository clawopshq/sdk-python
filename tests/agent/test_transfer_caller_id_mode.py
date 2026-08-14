"""전환받는 쪽에 표시될 발신번호를 SDK 에서 고르는 경로 (`caller_id_mode`).

왜 있나: call-engine 은 `callerIdMode: 'account'|'original'` 을 이미 지원하고 게이트웨이는
  transfer 객체를 손대지 않고 넘긴다. 그런데 SDK 에 파라미터가 없어서, SDK 사용자가 원발신자
  승계를 하려면 `caller_id=call.from_number` 를 넘기는 수밖에 없었다.

  서버는 그 둘을 다르게 취급한다 — **번호는 지시, 모드는 선호**다. 승계할 수 없는 통화
  (KCT 직결 인입이 아니거나 정규화 불가 발신번호, 실측 인바운드의 약 1.5%)에서
  번호는 `UNOWNED_CALLER_ID` 로 **전환을 통째로 실패**시키고, 모드는 계정 번호로 내려앉고
  전환을 성사시킨다.

고정하는 불변식:
  ① 모드를 주면 payload 에 `callerIdMode` 로 실린다
  ② 안 주면 **키가 붙지 않는다** — 기본 동작이 오늘과 같아야 한다(기존 사용자 영향 0)
  ③ 잘못된 값은 SDK 가 막는다. 서버는 모르는 값을 조용히 무시하므로 오타가 no-op 이 된다
  ④ 번호와 모드를 같이 줘도 둘 다 그대로 실린다 — 우선순위 판단은 서버 몫이고,
     규칙을 SDK 에 한 벌 더 두지 않는다
"""
import pytest

from clawops.agent._session import CallSession
from clawops.agent.pipeline._builtin_tool_schemas import get_builtin_tool_schemas
from clawops.agent._builtin_tools import BuiltinTool


def make_call() -> tuple[CallSession, list[dict]]:
    call = CallSession(
        call_id="CA_t", from_number="01040494897", to_number="07012341234", account_id="AC"
    )
    sent: list[dict] = []

    async def transfer_fn(params: dict) -> dict:
        sent.append(params)
        return {"status": "completed"}

    call._transfer_fn = transfer_fn
    return call, sent


@pytest.mark.asyncio
async def test_mode_rides_on_the_payload():
    call, sent = make_call()

    await call.transfer("15990011", caller_id_mode="original")

    assert sent[0]["callerIdMode"] == "original"


@pytest.mark.asyncio
async def test_omitting_the_mode_adds_no_key():
    """구 서버·기존 사용자에게 오늘과 똑같이 보여야 한다."""
    call, sent = make_call()

    await call.transfer("15990011")

    assert "callerIdMode" not in sent[0], (
        "안 준 필드를 만들어 보내면 기존 전환의 발신번호 동작이 바뀔 수 있다"
    )


@pytest.mark.asyncio
async def test_account_mode_is_also_sent_explicitly():
    """'account' 를 골랐다는 사실과 안 골랐다는 사실은 서버에서 구분돼야 한다."""
    call, sent = make_call()

    await call.transfer("15990011", caller_id_mode="account")

    assert sent[0]["callerIdMode"] == "account"


@pytest.mark.asyncio
async def test_typo_raises_instead_of_silently_doing_nothing():
    """서버는 'original' 외의 값을 조용히 무시한다 — 오타면 켠 줄 알고 070 이 나간다."""
    call, sent = make_call()

    with pytest.raises(ValueError, match="caller_id_mode"):
        await call.transfer("15990011", caller_id_mode="origianl")

    assert sent == [], "검증에 걸린 요청은 서버로 나가면 안 된다"


@pytest.mark.asyncio
async def test_number_and_mode_are_both_forwarded():
    """우선순위(번호가 이긴다)는 서버 규칙이다. SDK 는 판단하지 않고 그대로 보낸다."""
    call, sent = make_call()

    await call.transfer("15990011", caller_id="07012341234", caller_id_mode="original")

    assert sent[0]["callerId"] == "07012341234"
    assert sent[0]["callerIdMode"] == "original"


@pytest.mark.asyncio
async def test_payload_keys_match_the_node_sdk():
    """두 SDK 가 어긋나면 문서 한 벌이 두 곳에서 거짓이 된다.

    같은 목록이 clawops-node 의 tests/agent/transfer-caller-id-mode.test.ts 에도 있다.
    한쪽만 바뀌면 그쪽 테스트가 깨진다.
    """
    call, sent = make_call()

    await call.transfer("15990011", caller_id_mode="original")

    assert sorted(sent[0]) == sorted([
        "afterTransfer",
        "callerId",
        "callerIdMode",
        "context",
        "destinationType",
        "holdMedia",
        "mode",
        "timeout",
        "to",
        "whisper",
    ])


def test_builtin_tool_exposes_the_mode():
    """모델이 번호를 지어내는 대신 의도를 고를 수 있어야 한다."""
    schemas = get_builtin_tool_schemas({BuiltinTool.TRANSFER_CALL}, fmt="chat")
    props = schemas[0]["function"]["parameters"]["properties"]

    assert props["caller_id_mode"]["enum"] == ["account", "original"]
    # 원시 번호 경로는 남겨 둔다(프롬프트에서 쓰는 사용자를 깨뜨리지 않는다). 다만
    # 설명이 실패 결과를 분명히 해야 모델이 함부로 고르지 않는다.
    assert "fails the transfer" in props["caller_id"]["description"]
