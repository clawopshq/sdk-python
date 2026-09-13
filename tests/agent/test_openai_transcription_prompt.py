"""OpenAIRealtime(transcription_prompt=...) 가 전사 설정에 실리는지 — 실제 session.update 페이로드로 본다."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clawops.agent.pipeline.realtime._openai import OpenAIRealtime


def _make_mock_connection() -> MagicMock:
    conn = MagicMock()
    conn.session = MagicMock()
    conn.session.update = AsyncMock()
    conn.response = MagicMock()
    conn.response.create = AsyncMock()
    conn.close = AsyncMock()

    async def _aiter():
        if False:
            yield None
        return

    conn.__aiter__ = lambda self_: _aiter()
    return conn


async def _sent_transcription(sess: OpenAIRealtime) -> dict[str, Any]:
    conn = _make_mock_connection()
    with patch.object(sess, "_open_connection", new=AsyncMock(return_value=conn)):
        await sess.prewarm()
    await sess.stop()
    session = conn.session.update.await_args.kwargs["session"]
    return session["audio"]["input"]["transcription"]


def test_default_is_none() -> None:
    assert OpenAIRealtime(api_key="sk-test")._config.transcription_prompt is None


@pytest.mark.asyncio
async def test_prompt_is_sent_to_transcription() -> None:
    sess = OpenAIRealtime(api_key="sk-test", greeting=False, transcription_prompt="재진, 초진, 직원 연결")
    transcription = await _sent_transcription(sess)
    assert transcription["prompt"] == "재진, 초진, 직원 연결"
    # 기존 키는 그대로
    assert transcription["model"] == "gpt-4o-transcribe"
    assert transcription["language"] == "ko"


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", [None, ""])
async def test_no_prompt_key_when_unset(prompt: str | None) -> None:
    sess = OpenAIRealtime(api_key="sk-test", greeting=False, transcription_prompt=prompt)
    transcription = await _sent_transcription(sess)
    assert "prompt" not in transcription
