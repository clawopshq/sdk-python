# 서버 call.incoming 의 callerIdRestricted(clawops#1249) — 발신자가 번호 표시제한을 걸었나.
from unittest.mock import AsyncMock

import pytest
from clawops.agent import ClawOpsAgent
from clawops.agent._session import CallSession
from clawops.agent.pipeline.realtime._openai import OpenAIRealtime
from clawops.types.call import Call


def _agent() -> ClawOpsAgent:
    agent = ClawOpsAgent(
        api_key="sk_test",
        account_id="AC_test",
        from_="07012341234",
        session=OpenAIRealtime(api_key="sk-openai-test"),
    )
    # 미디어 세션 시작은 이 테스트의 관심 밖 — 세션 필드만 본다.
    agent._safe_start_call_session = AsyncMock()  # type: ignore[method-assign]
    return agent


async def _incoming(data: dict) -> CallSession:
    agent = _agent()
    await agent._handle_incoming({"event": "call.incoming", "mediaUrl": "", **data})
    return agent._active_sessions[data["callId"]]


@pytest.mark.asyncio
async def test_true_is_carried_and_number_is_unchanged():
    call = await _incoming({"callId": "C1", "from": "01062915351", "callerIdRestricted": True})
    assert call.caller_id_restricted is True
    assert call.from_number == "01062915351"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [{"callerIdRestricted": False}, {}, {"callerIdRestricted": "true"}],
    ids=["false", "old-server-no-key", "non-bool"],
)
async def test_false_missing_or_non_bool_is_false(extra):
    call = await _incoming({"callId": "C2", "from": "010", **extra})
    assert call.caller_id_restricted is False


def test_outbound_session_defaults_to_false():
    call = CallSession(call_id="C3", from_number="070", to_number="010", account_id="AC", direction="outbound")
    assert call.caller_id_restricted is False


def test_rest_call_model_parses_caller_id_restricted():
    base = {
        "callId": "CA1",
        "status": "completed",
        "to": "07052767664",
        "from": "01062915351",
        "direction": "inbound",
        "accountId": "AC1",
        "dateCreated": "2026-09-29T00:00:00Z",
    }
    assert Call.model_validate({**base, "callerIdRestricted": True}).caller_id_restricted is True
    assert Call.model_validate({**base, "callerIdRestricted": None}).caller_id_restricted is None
    assert Call.model_validate(base).caller_id_restricted is None
