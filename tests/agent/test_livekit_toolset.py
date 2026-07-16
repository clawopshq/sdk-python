"""ClawOpsPhoneTools + LiveKitSession 검증 가드 테스트."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("livekit.agents", reason="clawops[livekit] 미설치")

from clawops.agent._session import CallSession  # noqa: E402
from clawops.agent.livekit._session import _validate  # noqa: E402
from clawops.agent.livekit._toolset import ClawOpsPhoneTools  # noqa: E402


def _make_call() -> CallSession:
    call = CallSession(
        call_id="c", from_number="010", to_number="070", account_id="AC"
    )
    call._transfer_fn = AsyncMock(return_value={"status": "ok"})
    call._hangup_fn = AsyncMock()
    return call


class _Caps:
    def __init__(self, audio_output: bool) -> None:
        self.audio_output = audio_output


class _RealtimeLLM:
    def __init__(self, audio_output: bool) -> None:
        self.capabilities = _Caps(audio_output)


class _FakeSession:
    def __init__(self, llm=None, tts=None) -> None:
        self.llm = llm
        self.tts = tts


class _FakeAgent:
    # Agent 쪽 컴포넌트는 미설정 시 NOT_GIVEN 이지만, 여기선 세션이 이긴다.
    llm = None
    tts = None


# ── _validate 가드 ──────────────────────────────────────────────


def test_text_modality_without_tts_raises() -> None:
    """modalities=['text'] + tts 없음 = 말 못 하는 에이전트. 우리가 막는다."""
    session = _FakeSession(llm=_RealtimeLLM(audio_output=False), tts=None)
    with pytest.raises(ValueError, match="소리를 내지 못합니다"):
        _validate(session, _FakeAgent())


def test_text_modality_with_tts_ok() -> None:
    session = _FakeSession(llm=_RealtimeLLM(audio_output=False), tts=object())
    _validate(session, _FakeAgent())  # 예외 없음


def test_audio_modality_with_tts_warns(caplog: pytest.LogCaptureFixture) -> None:
    """modalities=['audio'] + tts = TTS 가 조용히 무시됨. 경고를 띄운다."""
    session = _FakeSession(llm=_RealtimeLLM(audio_output=True), tts=object())
    with caplog.at_level("WARNING"):
        _validate(session, _FakeAgent())
    assert any("무시" in r.message for r in caplog.records)


def test_non_realtime_llm_skips_guard() -> None:
    """일반 LLM(파이프라인)은 audio_output capability 가 없다 — 통과."""
    session = _FakeSession(llm=object(), tts=None)
    _validate(session, _FakeAgent())  # 예외 없음


# ── transfer_call 인자 검증 (기존 버그 회귀 방지) ──────────────


async def test_transfer_rejects_bad_destination_type_before_firing() -> None:
    """잘못된 인자를 fire-and-forget 전에 잡아야 한다.

    _builtin_tool_schemas.py:194 는 ensure_future 를 try/except 로 감쌌지만,
    ensure_future 는 즉시 반환하므로 인자 오류가 future 안에서 조용히 터진다.
    여기서는 transfer_fn 이 아예 호출되지 않아야 한다.
    """
    tools = ClawOpsPhoneTools()
    call = _make_call()
    tools.set_call(call)

    result = await tools._transfer_call(None, to="010", destination_type="carrier")

    assert "Error" in result
    call._transfer_fn.assert_not_awaited()


async def test_transfer_rejects_empty_to() -> None:
    tools = ClawOpsPhoneTools()
    call = _make_call()
    tools.set_call(call)

    result = await tools._transfer_call(None, to="   ")

    assert "Error" in result
    call._transfer_fn.assert_not_awaited()


async def test_transfer_valid_args_fires() -> None:
    tools = ClawOpsPhoneTools()
    call = _make_call()
    tools.set_call(call)

    result = await tools._transfer_call(None, to="01012345678")

    assert "transfer_initiated" in result
    await asyncio.sleep(0)  # fire-and-forget 태스크가 돌 틈
    call._transfer_fn.assert_awaited_once()


async def test_tool_before_call_bound_errors_cleanly() -> None:
    tools = ClawOpsPhoneTools()
    with pytest.raises(RuntimeError, match="연결되지 않았"):
        await tools._hang_up(None)
