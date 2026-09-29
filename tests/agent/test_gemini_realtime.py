# tests/agent/test_gemini_realtime.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clawops.agent.pipeline.realtime._gemini import GeminiRealtime


def test_gemini_realtime_init():
    session = GeminiRealtime(
        api_key="AIza-test",
        system_prompt="You are a helpful assistant.",
        model="gemini-3.1-flash-live-preview",
        voice="Kore",
    )
    assert session._api_key == "AIza-test"
    assert session._system_prompt == "You are a helpful assistant."
    assert session._model == "gemini-3.1-flash-live-preview"
    assert session._voice == "Kore"


def test_gemini_realtime_defaults():
    session = GeminiRealtime(api_key="AIza-test")
    assert session._model == "gemini-3.1-flash-live-preview"
    assert session._voice == "Kore"
    assert session._language == "ko"
    assert session._greeting is True


def test_gemini_realtime_tool_schemas():
    session = GeminiRealtime(api_key="AIza-test")
    schemas = session._build_tool_schemas()
    # hang_up tool은 항상 포함
    assert any(t["name"] == "hang_up" for t in schemas)


@pytest.mark.asyncio
async def test_gemini_sdk_start_connects():
    """SDK의 live.connect가 올바른 config로 호출되는지 검증."""
    session = GeminiRealtime(
        api_key="AIza-test",
        system_prompt="Test prompt",
        voice="Kore",
    )

    mock_live_session = AsyncMock()

    async def _empty_iter():
        session._session = None  # receive loop 종료
        if False:
            yield  # async generator that yields nothing

    mock_live_session.receive = _empty_iter
    mock_live_session.send_client_content = AsyncMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_live_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    captured_kwargs: dict = {}

    def capture_connect(**kwargs):
        captured_kwargs.update(kwargs)
        return mock_ctx

    session._client = MagicMock()
    session._client.aio.live.connect = capture_connect

    mock_call = MagicMock()
    mock_call._emit = AsyncMock()

    await session.start(mock_call)

    # SDK connect 호출 확인
    assert "model" in captured_kwargs
    assert captured_kwargs["model"] == "gemini-3.1-flash-live-preview"

    config = captured_kwargs["config"]

    # 핵심 config 확인
    assert config["response_modalities"] == ["AUDIO"]
    assert config["speech_config"]["voice_config"]["prebuilt_voice_config"]["voice_name"] == "Kore"
    assert config["system_instruction"].parts[0].text == "Test prompt"

    # Transcription (Stage 2)
    assert "input_audio_transcription" in config
    assert "output_audio_transcription" in config

    # Stage 3 필드가 포함되지 않았는지 확인
    assert "context_window_compression" not in config
    assert "realtime_input_config" not in config
    assert "thinking_config" not in config

    # 인사 메시지 전송 확인
    mock_live_session.send_realtime_input.assert_called_once()

    # cleanup
    await session.stop()


@pytest.mark.asyncio
async def test_gemini_sdk_thinking_config_passthrough():
    """thinking_config 는 해석하지 않고 connect config 에 그대로 실린다.

    gemini-3.8-live-extended-thinking 은 thinking_level 이 없으면 연결을 거절한다.
    """
    thinking = {"thinking_level": "LOW"}
    session = GeminiRealtime(
        api_key="AIza-test",
        model="gemini-3.8-live-extended-thinking",
        thinking_config=thinking,
        greeting=False,
    )

    mock_live_session = AsyncMock()

    async def _empty_iter():
        session._session = None
        if False:
            yield

    mock_live_session.receive = _empty_iter

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_live_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    captured_kwargs: dict = {}

    def capture_connect(**kwargs):
        captured_kwargs.update(kwargs)
        return mock_ctx

    session._client = MagicMock()
    session._client.aio.live.connect = capture_connect

    await session.start(MagicMock())

    assert captured_kwargs["model"] == "gemini-3.8-live-extended-thinking"
    assert captured_kwargs["config"]["thinking_config"] == thinking

    await session.stop()


@pytest.mark.asyncio
async def test_gemini_sdk_feed_audio():
    """SDK의 send_realtime_input으로 오디오가 전송되는지 검증."""
    session = GeminiRealtime(api_key="AIza-test")

    mock_live_session = AsyncMock()
    mock_live_session.send_realtime_input = AsyncMock()
    session._session = mock_live_session
    session._call = MagicMock()

    # G.711 ulaw 160 bytes (20ms at 8kHz, silence)
    ulaw_chunk = b"\xff" * 160
    await session.feed_audio(ulaw_chunk, timestamp=0)

    # SDK send_realtime_input 호출 확인
    mock_live_session.send_realtime_input.assert_called_once()
    call_kwargs = mock_live_session.send_realtime_input.call_args
    blob = call_kwargs.kwargs.get("audio") or call_kwargs.args[0]
    assert blob.mime_type == "audio/pcm;rate=16000"
    assert isinstance(blob.data, bytes)
    # PCM16 16kHz: 160 ulaw samples → 160 PCM16 samples → 320 PCM16 16kHz samples = 640 bytes
    assert len(blob.data) == 640
