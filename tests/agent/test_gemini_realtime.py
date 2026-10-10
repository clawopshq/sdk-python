# tests/agent/test_gemini_realtime.py
import asyncio
from unittest.mock import AsyncMock, MagicMock

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
    assert "proactivity" not in config

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


# ── 3.8 Live 함수 호출 behavior · proactive audio · api_version (0.60.0) ─────────────


def _capture_connect(session: GeminiRealtime) -> tuple[dict, AsyncMock]:
    """live.connect 호출 kwargs 를 잡고, receive 가 즉시 끝나는 mock 세션을 붙인다."""
    mock_live_session = AsyncMock()

    async def _empty_iter():
        session._session = None
        if False:
            yield

    mock_live_session.receive = _empty_iter

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_live_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    captured: dict = {}

    def capture_connect(**kwargs):
        captured.update(kwargs)
        return mock_ctx

    session._client = MagicMock()
    session._client.aio.live.connect = capture_connect
    return captured, mock_live_session


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gemini-3.8-live", "BLOCKING"),
        ("models/gemini-3.8-live", "BLOCKING"),
        ("gemini-3.8-live-extended-thinking", "NON_BLOCKING"),
        ("gemini-3.1-flash-live-preview", None),
        ("gemini-2.5-flash-native-audio-preview-12-2025", None),
    ],
)
def test_default_tool_behavior_by_model(model, expected):
    """3.8-live 는 서버 기본값이 NON_BLOCKING 이라 BLOCKING 을 명시하고, ET 는 NON_BLOCKING 만 받는다."""
    from clawops.agent.pipeline.realtime._gemini import default_tool_behavior

    assert default_tool_behavior(model) == expected


def test_tool_schemas_carry_model_default_behavior_on_3_8():
    session = GeminiRealtime(api_key="AIza-test", model="gemini-3.8-live")

    @session._tools.register
    async def lookup_order(order_id: str) -> str:
        """주문 조회"""
        return "ok"

    schemas = session._build_tool_schemas()
    names = {d["name"] for d in schemas}
    assert {"lookup_order", "hang_up"} <= names
    # 커스텀·내장 가리지 않고 전부 BLOCKING — 내장 도구(hang_up 등)를 모델이 말로 덮지 않게.
    assert all(d["behavior"] == "BLOCKING" for d in schemas)


def test_tool_schemas_omit_behavior_when_model_has_no_default():
    session = GeminiRealtime(api_key="AIza-test", model="gemini-3.1-flash-live-preview")
    assert all("behavior" not in d for d in session._build_tool_schemas())


def test_tool_schemas_extended_thinking_is_non_blocking():
    session = GeminiRealtime(api_key="AIza-test", model="gemini-3.8-live-extended-thinking")
    assert all(d["behavior"] == "NON_BLOCKING" for d in session._build_tool_schemas())


def test_explicit_tool_behavior_overrides_model_default():
    session = GeminiRealtime(api_key="AIza-test", model="gemini-3.8-live", tool_behavior="NON_BLOCKING")

    @session._tools.register
    async def lookup(order_id: str) -> str:
        """조회"""
        return "ok"

    assert session.effective_tool_behavior() == "NON_BLOCKING"
    by_name = {d["name"]: d for d in session._build_tool_schemas()}
    assert by_name["lookup"]["behavior"] == "NON_BLOCKING"
    # 내장 도구(통화 제어)는 세션 기본값이 NON_BLOCKING 이어도 BLOCKING 을 지킨다.
    assert by_name["hang_up"]["behavior"] == "BLOCKING"


def test_invalid_tool_behavior_raises():
    with pytest.raises(ValueError, match="tool_behavior"):
        GeminiRealtime(api_key="AIza-test", tool_behavior="ASYNC")


@pytest.mark.asyncio
async def test_proactive_audio_passthrough():
    """proactive_audio 는 해석하지 않고 proactivity.proactive_audio 에 그대로 실린다."""
    session = GeminiRealtime(api_key="AIza-test", model="gemini-3.8-live", proactive_audio=True, greeting=False)
    captured, _ = _capture_connect(session)
    await session.start(MagicMock())
    assert captured["config"]["proactivity"] == {"proactive_audio": True}
    await session.stop()


@pytest.mark.asyncio
async def test_proactive_audio_absent_by_default():
    session = GeminiRealtime(api_key="AIza-test", model="gemini-3.8-live", greeting=False)
    captured, _ = _capture_connect(session)
    await session.start(MagicMock())
    assert "proactivity" not in captured["config"]
    await session.stop()


@pytest.mark.asyncio
async def test_tool_behavior_reaches_connect_config():
    session = GeminiRealtime(api_key="AIza-test", model="gemini-3.8-live", greeting=False)
    captured, _ = _capture_connect(session)
    await session.start(MagicMock())
    decls = captured["config"]["tools"][0]["function_declarations"]
    assert decls and all(d["behavior"] == "BLOCKING" for d in decls)
    await session.stop()


def test_api_version_reaches_genai_client(monkeypatch):
    import clawops.agent.pipeline.realtime._gemini as mod

    seen: dict = {}

    def fake_client(**kwargs):
        seen.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(mod.genai, "Client", fake_client)
    GeminiRealtime(api_key="AIza-test", api_version="v1alpha")
    assert seen["api_key"] == "AIza-test"
    assert seen["http_options"] == {"api_version": "v1alpha"}

    seen.clear()
    GeminiRealtime(api_key="AIza-test")
    assert "http_options" not in seen


# ── 도구별 behavior/scheduling + NON_BLOCKING 런타임 (0.61.0) ────────────────────
from types import SimpleNamespace

from google.genai import types as genai_types

from clawops.agent._tool import ToolRegistry


def _runtime_session(model: str = "gemini-3.8-live", **kwargs) -> GeminiRealtime:
    """연결 없이 수신 경로만 시험할 세션. _build_tool_schemas 로 선언 표를 채운 뒤 돌려준다."""
    session = GeminiRealtime(api_key="AIza-test", model=model, greeting=False, **kwargs)
    session._session = AsyncMock()
    call = MagicMock()
    call._emit = AsyncMock()
    call.send_audio = AsyncMock()
    call.clear_audio = AsyncMock()
    session._call = call
    return session


def _tool_call_msg(*calls: genai_types.FunctionCall):
    return SimpleNamespace(
        server_content=None,
        tool_call=genai_types.LiveServerToolCall(function_calls=list(calls)),
        tool_call_cancellation=None,
    )


async def _settle(n: int = 5) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


def test_registry_register_accepts_behavior_and_scheduling_in_both_decorator_forms():
    registry = ToolRegistry()

    @registry.register
    async def plain(x: str) -> str:
        """plain"""
        return x

    @registry.register(behavior="NON_BLOCKING", scheduling="INTERRUPT")
    async def async_tool(x: str) -> str:
        """async"""
        return x

    assert registry["plain"].behavior is None and registry["plain"].scheduling is None
    assert registry["async_tool"].behavior == "NON_BLOCKING"
    assert registry["async_tool"].scheduling == "INTERRUPT"
    # OpenAI 스키마에는 새 필드가 새지 않는다.
    assert all("behavior" not in t and "scheduling" not in t for t in registry.to_openai_tools())


def test_registry_rejects_bad_behavior_or_scheduling():
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="behavior"):
        registry.register(behavior="ASYNC")
    with pytest.raises(ValueError, match="scheduling"):
        registry.register(scheduling="LATER")


def test_agent_tool_decorator_both_forms():
    from clawops.agent import ClawOpsAgent

    agent = ClawOpsAgent(
        api_key="ck_test",
        account_id="ACtest",
        from_="07012341234",
        session=GeminiRealtime(api_key="AIza-test"),
    )

    @agent.tool
    async def a(x: str) -> str:
        """a"""
        return x

    @agent.tool(behavior="NON_BLOCKING")
    async def b(x: str) -> str:
        """b"""
        return x

    assert agent._tool_registry["a"].behavior is None
    assert agent._tool_registry["b"].behavior == "NON_BLOCKING"


def test_per_tool_behavior_overrides_session_default():
    session = GeminiRealtime(api_key="AIza-test", model="gemini-3.8-live")

    @session._tools.register(behavior="NON_BLOCKING", scheduling="WHEN_IDLE")
    async def save_note(text: str) -> str:
        """메모 저장"""
        return "saved"

    @session._tools.register
    async def lookup(order_id: str) -> str:
        """조회"""
        return "ok"

    by_name = {d["name"]: d for d in session._build_tool_schemas()}
    assert by_name["save_note"]["behavior"] == "NON_BLOCKING"
    assert by_name["lookup"]["behavior"] == "BLOCKING"
    assert by_name["hang_up"]["behavior"] == "BLOCKING"
    assert session._declared_scheduling["save_note"] == "WHEN_IDLE"


def test_extended_thinking_forces_non_blocking_even_when_tool_asks_blocking(caplog):
    session = GeminiRealtime(api_key="AIza-test", model="gemini-3.8-live-extended-thinking")

    @session._tools.register(behavior="BLOCKING")
    async def lookup(order_id: str) -> str:
        """조회"""
        return "ok"

    with caplog.at_level("WARNING"):
        by_name = {d["name"]: d for d in session._build_tool_schemas()}
    assert by_name["lookup"]["behavior"] == "NON_BLOCKING"
    assert by_name["hang_up"]["behavior"] == "NON_BLOCKING"
    assert any("accepts NON_BLOCKING only" in r.message for r in caplog.records)


def test_invalid_tool_response_scheduling_raises():
    with pytest.raises(ValueError, match="tool_response_scheduling"):
        GeminiRealtime(api_key="AIza-test", tool_response_scheduling="LATER")


@pytest.mark.asyncio
async def test_non_blocking_tool_runs_without_blocking_receive_and_replies_when_done():
    session = _runtime_session()
    gate = asyncio.Event()

    @session._tools.register(behavior="NON_BLOCKING")
    async def slow(x: str) -> str:
        """slow"""
        await gate.wait()
        return "done"

    session._build_tool_schemas()
    await session._handle_response(_tool_call_msg(genai_types.FunctionCall(name="slow", id="c1", args={"x": "1"})))

    # drain 대기 묶음에 들어가지 않고 즉시 태스크로 떴다.
    assert session._pending_tool_call is None
    assert "c1" in session._inflight

    # 도구가 도는 동안 수신 경로는 살아 있다 — 전사가 그대로 전달된다.
    await session._handle_response(
        SimpleNamespace(
            server_content=SimpleNamespace(
                model_turn=None,
                turn_complete=False,
                generation_complete=False,
                interrupted=False,
                input_transcription=None,
                output_transcription=SimpleNamespace(text="잠시만요"),
            ),
            tool_call=None,
            tool_call_cancellation=None,
        )
    )
    session._call._emit.assert_awaited_with("transcript", "assistant", "잠시만요")
    session._session.send_tool_response.assert_not_called()

    gate.set()
    await _settle()
    session._session.send_tool_response.assert_awaited_once()
    (resp,) = session._session.send_tool_response.call_args.kwargs["function_responses"]
    assert resp.id == "c1" and resp.name == "slow"
    assert resp.response == {"result": "done"}
    assert resp.scheduling is None  # 결과가 있고 따로 정한 값이 없으면 서버 기본값(WHEN_IDLE)
    assert "c1" not in session._inflight
    await session.stop()


@pytest.mark.asyncio
async def test_non_blocking_tool_without_result_is_sent_silent():
    session = _runtime_session()

    @session._tools.register(behavior="NON_BLOCKING")
    async def fire_and_forget(x: str):
        """결과 없음"""
        return

    session._build_tool_schemas()
    await session._handle_response(
        _tool_call_msg(genai_types.FunctionCall(name="fire_and_forget", id="c2", args={"x": "1"}))
    )
    await _settle()
    (resp,) = session._session.send_tool_response.call_args.kwargs["function_responses"]
    assert resp.response == {"result": "ok"}
    assert resp.scheduling == "SILENT"
    await session.stop()


@pytest.mark.asyncio
async def test_non_blocking_scheduling_precedence_tool_then_session():
    session = _runtime_session(tool_response_scheduling="WHEN_IDLE")

    @session._tools.register(behavior="NON_BLOCKING", scheduling="INTERRUPT")
    async def urgent(x: str) -> str:
        """urgent"""
        return "r"

    @session._tools.register(behavior="NON_BLOCKING")
    async def normal(x: str) -> str:
        """normal"""
        return "r"

    session._build_tool_schemas()
    await session._handle_response(
        _tool_call_msg(
            genai_types.FunctionCall(name="urgent", id="u", args={"x": "1"}),
            genai_types.FunctionCall(name="normal", id="n", args={"x": "1"}),
        )
    )
    await _settle()
    sent = {
        r.name: r.scheduling
        for c in session._session.send_tool_response.call_args_list
        for r in c.kwargs["function_responses"]
    }
    assert sent == {"urgent": "INTERRUPT", "normal": "WHEN_IDLE"}
    await session.stop()


@pytest.mark.asyncio
async def test_extended_thinking_never_sends_scheduling(caplog):
    session = _runtime_session(model="gemini-3.8-live-extended-thinking", tool_response_scheduling="SILENT")

    @session._tools.register
    async def note(x: str):
        """note"""
        return

    session._build_tool_schemas()
    with caplog.at_level("WARNING"):
        await session._handle_response(_tool_call_msg(genai_types.FunctionCall(name="note", id="e1", args={"x": "1"})))
        await _settle()
    (resp,) = session._session.send_tool_response.call_args.kwargs["function_responses"]
    assert resp.scheduling is None
    assert any("rejects FunctionResponse.scheduling" in r.message for r in caplog.records)
    await session.stop()


@pytest.mark.asyncio
async def test_tool_call_cancellation_cancels_inflight_and_sends_nothing():
    session = _runtime_session()
    gate = asyncio.Event()

    @session._tools.register(behavior="NON_BLOCKING")
    async def slow(x: str) -> str:
        """slow"""
        await gate.wait()
        return "done"

    session._build_tool_schemas()
    await session._handle_response(_tool_call_msg(genai_types.FunctionCall(name="slow", id="c1", args={"x": "1"})))
    await _settle()
    task = session._inflight["c1"]

    await session._handle_response(
        SimpleNamespace(server_content=None, tool_call=None, tool_call_cancellation=SimpleNamespace(ids=["c1"]))
    )
    await _settle()
    assert task.cancelled()
    assert "c1" not in session._inflight
    gate.set()
    await _settle()
    session._session.send_tool_response.assert_not_called()
    await session.stop()


@pytest.mark.asyncio
async def test_blocking_tool_still_goes_through_drain_path():
    session = _runtime_session()

    @session._tools.register
    async def lookup(x: str) -> str:
        """lookup"""
        return "ok"

    session._build_tool_schemas()
    msg = _tool_call_msg(genai_types.FunctionCall(name="lookup", id="b1", args={"x": "1"}))
    await session._handle_response(msg)
    assert session._pending_tool_call is msg.tool_call
    assert not session._inflight
    await session.stop()


@pytest.mark.asyncio
async def test_mixed_tool_call_splits_blocking_and_non_blocking():
    session = _runtime_session()

    @session._tools.register(behavior="NON_BLOCKING")
    async def note(x: str) -> str:
        """note"""
        return "saved"

    @session._tools.register
    async def lookup(x: str) -> str:
        """lookup"""
        return "ok"

    session._build_tool_schemas()
    await session._handle_response(
        _tool_call_msg(
            genai_types.FunctionCall(name="note", id="n1", args={"x": "1"}),
            genai_types.FunctionCall(name="lookup", id="l1", args={"x": "1"}),
        )
    )
    assert [fc.name for fc in session._pending_tool_call.function_calls] == ["lookup"]
    await _settle()
    assert session._session.send_tool_response.await_count == 1
    await session.stop()


@pytest.mark.asyncio
async def test_response_omits_id_when_server_sent_none():
    session = _runtime_session()

    @session._tools.register(behavior="NON_BLOCKING")
    async def note(x: str) -> str:
        """note"""
        return "saved"

    session._build_tool_schemas()
    await session._handle_response(_tool_call_msg(genai_types.FunctionCall(name="note", args={"x": "1"})))
    await _settle()
    (resp,) = session._session.send_tool_response.call_args.kwargs["function_responses"]
    assert resp.id is None
    await session.stop()


@pytest.mark.asyncio
async def test_generation_complete_flushes_audio_remainder():
    session = _runtime_session()
    session._audio_remainder = b"\x01" * 10
    await session._handle_response(
        SimpleNamespace(
            server_content=SimpleNamespace(
                model_turn=None,
                turn_complete=False,
                generation_complete=True,
                interrupted=False,
                input_transcription=None,
                output_transcription=None,
            ),
            tool_call=None,
            tool_call_cancellation=None,
        )
    )
    session._call.send_audio.assert_awaited_once()
    (frame,) = session._call.send_audio.call_args.args
    assert len(frame) == 160 and frame.startswith(b"\x01" * 10)
    assert session._audio_remainder == b""


# ── 세션 수명: session_resumption · go_away 재접속 · session_lost (0.62.0) ──────────


def _lifecycle_msg(**fields):
    base = {
        "server_content": None,
        "tool_call": None,
        "tool_call_cancellation": None,
        "session_resumption_update": None,
        "go_away": None,
    }
    base.update(fields)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_connect_config_sends_session_resumption_by_default():
    session = GeminiRealtime(api_key="AIza-test", model="gemini-3.8-live", greeting=False)
    captured, _ = _capture_connect(session)
    await session.start(MagicMock())
    assert captured["config"]["session_resumption"] == {"handle": None}
    assert "context_window_compression" not in captured["config"]
    await session.stop()


@pytest.mark.asyncio
async def test_session_resumption_can_be_disabled_and_compression_passthrough():
    compression = {"sliding_window": {}}
    session = GeminiRealtime(
        api_key="AIza-test",
        model="gemini-3.8-live",
        greeting=False,
        session_resumption=False,
        context_window_compression=compression,
    )
    captured, _ = _capture_connect(session)
    await session.start(MagicMock())
    assert "session_resumption" not in captured["config"]
    assert captured["config"]["context_window_compression"] == compression
    await session.stop()


@pytest.mark.asyncio
async def test_resumption_update_stores_handle_and_go_away_reconnects_with_it():
    session = _runtime_session()
    old_ctx = AsyncMock()
    session._live_ctx = old_ctx
    old_session = session._session
    new_session = AsyncMock()
    opened: list[str | None] = []

    async def fake_open():
        opened.append(session._resumption_handle)
        return new_session

    session._open_live_session = fake_open  # type: ignore[method-assign]

    await session._handle_response(
        _lifecycle_msg(session_resumption_update=SimpleNamespace(resumable=True, new_handle="h-123"))
    )
    assert session._resumption_handle == "h-123"

    await session._handle_response(_lifecycle_msg(go_away=SimpleNamespace(time_left="10s")))
    await _settle(10)
    assert opened == ["h-123"]  # 새 접속은 보관한 핸들로
    assert session._session is new_session and session._session is not old_session
    old_ctx.__aexit__.assert_awaited_once()
    await session.stop()


@pytest.mark.asyncio
async def test_receive_loop_reconnects_when_connection_drops():
    session = _runtime_session()

    class FirstSession:
        async def receive(self):
            raise ConnectionError("1011 going away")
            yield  # pragma: no cover

    second = AsyncMock()

    async def _second_receive():
        session._stopping = True  # 한 번 돈 뒤 루프를 끝낸다
        if False:
            yield

    second.receive = _second_receive
    session._session = FirstSession()
    session._open_live_session = AsyncMock(return_value=second)  # type: ignore[method-assign]

    await session._receive_loop()
    session._open_live_session.assert_awaited_once()
    assert session._session is second
    session._call.hangup.assert_not_called()


@pytest.mark.asyncio
async def test_reconnect_failure_emits_session_lost_and_hangs_up():
    session = _runtime_session(reconnect_max_attempts=1)
    session._resumption_handle = "h-1"
    session._open_live_session = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    session._call.hangup = AsyncMock()

    ok = await session._reconnect("test")
    assert ok is False
    # 핸들 시도 1회 + 핸들 없는 새 세션 시도 1회
    assert session._open_live_session.await_count == 2
    session._call._emit.assert_awaited()
    event, reason = session._call._emit.call_args.args
    assert event == "session_lost" and "reconnect failed" in reason
    session._call.hangup.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconnect_falls_back_to_fresh_session_when_resume_is_rejected():
    """핸들 재접속이 1008(already connected)로 계속 거절되면 핸들 없이 새 세션으로 통화를 살린다."""
    session = _runtime_session(reconnect_max_attempts=1)
    session._resumption_handle = "h-1"
    fresh = AsyncMock()
    seen: list[str | None] = []

    async def fake_open():
        seen.append(session._resumption_handle)
        if session._resumption_handle:
            raise RuntimeError("1008 session is already connected to an existing client")
        return fresh

    session._open_live_session = fake_open  # type: ignore[method-assign]
    ok = await session._reconnect("test")
    assert ok is True
    assert seen == ["h-1", None]
    assert session._session is fresh
    session._call._emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconnect_closes_connections_of_failed_attempts():
    """타임아웃·거절로 버린 시도의 연결(self._live_ctx)도 닫는다 — 안 닫으면 서버가 세션을 붙잡는다."""
    session = _runtime_session(reconnect_max_attempts=1)
    old_ctx = AsyncMock()
    session._live_ctx = old_ctx
    abandoned = AsyncMock()
    good = AsyncMock()
    calls = {"n": 0}

    async def fake_open():
        calls["n"] += 1
        if calls["n"] == 1:
            session._live_ctx = abandoned  # 연결은 열렸는데 setup 이 실패한 모양
            raise RuntimeError("setup timeout")
        session._live_ctx = AsyncMock()
        return good

    session._open_live_session = fake_open  # type: ignore[method-assign]
    session._resumption_handle = "h-1"
    ok = await session._reconnect("test")
    assert ok is True and session._session is good
    old_ctx.__aexit__.assert_awaited_once()  # 옛 연결은 재접속 전에 닫는다
    abandoned.__aexit__.assert_awaited_once()  # 실패한 시도의 연결도 닫는다


@pytest.mark.asyncio
async def test_go_away_ignored_while_stopping():
    session = _runtime_session()
    session._stopping = True
    session._open_live_session = AsyncMock()  # type: ignore[method-assign]
    await session._handle_response(_lifecycle_msg(go_away=SimpleNamespace(time_left="1s")))
    await _settle()
    session._open_live_session.assert_not_called()


@pytest.mark.asyncio
async def test_stale_connection_control_messages_are_ignored():
    """재접속으로 교체된 옛 연결의 go_away·핸들·tool_call 은 무시하고 전사는 흘린다."""
    session = _runtime_session()
    session._resumption_handle = "h-new"
    session._open_live_session = AsyncMock()  # type: ignore[method-assign]

    @session._tools.register(behavior="NON_BLOCKING")
    async def note(x: str) -> str:
        """note"""
        return "r"

    session._build_tool_schemas()
    msg = _lifecycle_msg(
        server_content=SimpleNamespace(
            model_turn=None,
            turn_complete=False,
            generation_complete=False,
            interrupted=False,
            input_transcription=None,
            output_transcription=SimpleNamespace(text="늦은 전사"),
        ),
        session_resumption_update=SimpleNamespace(resumable=True, new_handle="h-old"),
        go_away=SimpleNamespace(time_left="1s"),
        tool_call=genai_types.LiveServerToolCall(
            function_calls=[genai_types.FunctionCall(name="note", id="x", args={"x": "1"})]
        ),
    )
    await session._handle_response(msg, stale=True)
    await _settle()
    session._call._emit.assert_awaited_with("transcript", "assistant", "늦은 전사")
    assert session._resumption_handle == "h-new"
    session._open_live_session.assert_not_called()
    assert not session._inflight
    await session.stop()
