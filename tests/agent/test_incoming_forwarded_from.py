# 서버 call.incoming 의 forwardedFrom(clawops#1423) — 착신전환 통화에서 발신자가 원래 건 번호.
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from clawops.agent import ClawOpsAgent, IncomingCall
from clawops.agent._session import CallSession


def _agent() -> ClawOpsAgent:
    session = MagicMock()
    session.session_setup = AsyncMock()
    agent = ClawOpsAgent(api_key="sk_test", account_id="AC_test", from_="07012341234", session=session)
    # 미디어 세션 시작은 이 테스트의 관심 밖 — 세션 필드만 본다.
    agent._safe_start_call_session = AsyncMock()  # type: ignore[method-assign]
    agent._control_ws = MagicMock(send=AsyncMock())  # type: ignore[assignment]
    return agent


async def _incoming(data: dict) -> CallSession:
    agent = _agent()
    await agent._handle_incoming({"event": "call.incoming", "mediaUrl": "", **data})
    return agent._active_sessions[data["callId"]]


@pytest.mark.asyncio
async def test_forwarded_from_is_carried():
    call = await _incoming({"callId": "C1", "from": "01011112222", "forwardedFrom": "01033334444"})
    assert call.forwarded_from == "01033334444"
    assert call.from_number == "01011112222"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [{}, {"forwardedFrom": ""}, {"forwardedFrom": None}, {"forwardedFrom": 123}],
    ids=["old-server-or-direct", "empty", "null", "non-str"],
)
async def test_missing_empty_or_non_str_is_none(extra):
    call = await _incoming({"callId": "C2", "from": "010", **extra})
    assert call.forwarded_from is None


def test_outbound_session_defaults_to_none():
    call = CallSession(call_id="C3", from_number="070", to_number="010", account_id="AC", direction="outbound")
    assert call.forwarded_from is None


@pytest.mark.asyncio
async def test_on_incoming_hook_sees_forwarded_from_before_answer():
    agent = _agent()
    seen: list[IncomingCall] = []

    @agent.on_incoming
    async def screen(call: IncomingCall):
        seen.append(call)

    await agent._handle_incoming(
        {"event": "call.incoming", "callId": "C4", "from": "010", "mediaUrl": "wss://m", "forwardedFrom": "01033334444"}
    )
    for _ in range(100):
        await asyncio.sleep(0.01)
        if not agent._screening:
            break
    await asyncio.sleep(0.01)
    assert seen[0].forwarded_from == "01033334444"
    assert agent._active_sessions["C4"].forwarded_from == "01033334444"
