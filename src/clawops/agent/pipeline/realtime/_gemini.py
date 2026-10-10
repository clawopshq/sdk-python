"""Google Gemini Live API 세션 (google-genai SDK 기반).

Session Protocol을 구현하며, google-genai SDK의 Live API를 사용한다.
입력 오디오는 G.711 ulaw → PCM16 16kHz로, 출력은 PCM16 24kHz → G.711 ulaw로 변환한다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

try:
    from google import genai
    from google.genai import types

    _HAS_GENAI = True
except ImportError:
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]
    _HAS_GENAI = False

from ..._audio import pcm16_to_ulaw, resample_pcm16, ulaw_to_pcm16
from ..._builtin_tools import BuiltinTool
from ..._hold_audio import HoldAudioPlayer
from ..._recorder import AudioRecorder
from ..._session import CallSession
from ..._tool import TOOL_BEHAVIORS, TOOL_SCHEDULINGS, ToolRegistry
from ...tracing._spans import llm_session_span, tool_call_span
from .._builtin_tool_schemas import BUILTIN_TOOL_NAMES, execute_builtin_tool, get_builtin_tool_schemas

log = logging.getLogger("clawops.agent")

_TRACE_ENABLED = os.environ.get("CLAWOPS_GEMINI_DEBUG", "").lower() in ("1", "true", "yes")
"""``CLAWOPS_GEMINI_DEBUG=1`` 이면 Live 서버 메시지를 한 줄씩 INFO 로 남긴다(오디오는 바이트 수만)."""


def is_extended_thinking_model(model: str) -> bool:
    return "extended-thinking" in model


def default_tool_behavior(model: str) -> str | None:
    """모델별 함수 호출 ``behavior`` 기본값.

    - ``gemini-3.8-live-extended-thinking`` 은 NON_BLOCKING 만 받고 BLOCKING 은 하드 에러다.
    - ``gemini-3.8-live`` 는 서버 기본값이 NON_BLOCKING 으로 바뀌었다(모델 페이지 「Migrating from
      Gemini 3.1 Flash Live」). behavior 를 안 보내면 서버는 도구를 비동기로 돌려, 도구 결과를
      기다리는 동안 모델이 계속 말한다. 전화 SDK 는 결과를 기다리는 동안 모델이 말하지 않아야
      하므로 BLOCKING 을 명시해 3.1 과 같은 동작을 복원한다.
    - 그 외(3.1 등)는 보내지 않는다(서버 기본값 = 동기).

    LiveKit `livekit-plugins-google` 의 `_default_tool_behavior` 와 같은 자리이되, 기본값 방향은
    반대다(LiveKit 은 3.8 에 NON_BLOCKING 을 명시한다).
    """
    if is_extended_thinking_model(model):
        return "NON_BLOCKING"
    if "3.8" in model:
        return "BLOCKING"
    return None


def _resolve_ref(ref: str, defs: dict[str, Any]) -> dict[str, Any]:
    """$ref 문자열을 $defs에서 찾아 반환한다."""
    parts = ref.lstrip("#/").split("/")
    result = defs
    for part in parts:
        if isinstance(result, dict):
            result = result.get(part, {})
        else:
            return {}
    return result if isinstance(result, dict) else {}


def _sanitize_schema_for_gemini(
    schema: dict[str, Any],
    defs: dict[str, Any] | None = None,
    _depth: int = 0,
) -> dict[str, Any]:
    """JSON Schema를 Gemini functionDeclarations 호환 형식으로 변환한다.

    - $ref를 인라인으로 resolve
    - oneOf/anyOf/allOf를 단순화 (첫 번째 object 타입 또는 첫 번째 항목 사용)
    - 지원되지 않는 키워드 제거
    - 재귀 깊이 제한으로 순환 참조 방지
    """
    if _depth > 15:
        return {"type": "object", "properties": {}}

    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    if defs is None:
        defs = schema.get("$defs", schema.get("definitions", {}))

    if "$ref" in schema:
        resolved = _resolve_ref(schema["$ref"], {"$defs": defs, "definitions": defs})
        if resolved:
            return _sanitize_schema_for_gemini(resolved, defs, _depth + 1)
        return {"type": "object", "properties": {}}

    for combo_key in ("oneOf", "anyOf", "allOf"):
        if combo_key in schema:
            variants = schema[combo_key]
            if isinstance(variants, list) and variants:
                for v in variants:
                    resolved_v = _sanitize_schema_for_gemini(v, defs, _depth + 1)
                    if resolved_v.get("type") == "object" and resolved_v.get("properties"):
                        return resolved_v
                return _sanitize_schema_for_gemini(variants[0], defs, _depth + 1)

    result: dict[str, Any] = {}

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        non_null = [t for t in schema_type if t != "null"]
        schema_type = non_null[0] if non_null else "string"

    if schema_type:
        result["type"] = schema_type

    if "description" in schema:
        result["description"] = schema["description"]

    if "enum" in schema:
        result["enum"] = schema["enum"]

    if "required" in schema:
        result["required"] = schema["required"]

    if "properties" in schema:
        props = {}
        for key, val in schema["properties"].items():
            if isinstance(val, dict):
                props[key] = _sanitize_schema_for_gemini(val, defs, _depth + 1)
        result["properties"] = props

    if "items" in schema:
        items = schema["items"]
        if isinstance(items, dict):
            result["items"] = _sanitize_schema_for_gemini(items, defs, _depth + 1)

    if "type" not in result and "properties" in result:
        result["type"] = "object"

    if result.get("type") == "object" and "properties" not in result:
        result["properties"] = {}

    return result


class GeminiRealtime:
    """Gemini Live API 기반 음성 세션 (google-genai SDK).

    Session Protocol을 구현한다.
    """

    tools_frozen_after_prewarm: bool = True
    """Gemini Live 는 connect 시점의 config 로 도구가 고정되어 세션 도중 변경할 수 없다.

    통화 시작 시점에야 붙는 MCP 도구는 prewarm 된 세션에 넣을 방법이 없으므로,
    `ClawOpsAgent` 가 MCP 서버 설정과 이 플래그를 보고 prewarm 을 건너뛴다.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        system_prompt: str = "",
        model: str = "gemini-3.1-flash-live-preview",
        voice: str = "Kore",
        language: str = "ko",
        greeting: bool = True,
        tool_registry: ToolRegistry | None = None,
        recorder: AudioRecorder | None = None,
        realtime_input_config: dict[str, Any] | None = None,
        thinking_config: dict[str, Any] | None = None,
        proactive_audio: bool | None = None,
        tool_behavior: str | None = None,
        tool_response_scheduling: str | None = None,
        api_version: str | None = None,
    ) -> None:
        if not _HAS_GENAI:
            raise ImportError(
                "google-genai is required for GeminiRealtime. Install it with: pip install clawops[gemini]"
            )
        if api_key is None:
            api_key = os.environ.get("GOOGLE_API_KEY")
        self._api_key = api_key
        self._system_prompt = system_prompt
        self._model = model
        self._voice = voice
        self._language = language
        self._greeting = greeting
        self._tools = tool_registry or ToolRegistry()
        self._builtin_tools: set[BuiltinTool] | None = None
        self._recorder = recorder
        self._realtime_input_config = realtime_input_config
        self._thinking_config = thinking_config
        self._proactive_audio = proactive_audio
        if tool_behavior is not None and tool_behavior not in TOOL_BEHAVIORS:
            raise ValueError(f"tool_behavior must be one of {TOOL_BEHAVIORS}, got {tool_behavior!r}")
        self._tool_behavior = tool_behavior
        if tool_response_scheduling is not None and tool_response_scheduling not in TOOL_SCHEDULINGS:
            raise ValueError(
                f"tool_response_scheduling must be one of {TOOL_SCHEDULINGS}, got {tool_response_scheduling!r}"
            )
        self._tool_response_scheduling = tool_response_scheduling
        self._api_version = api_version
        # 선언 시점에 확정한 도구별 behavior·scheduling. 응답을 보낼 때 같은 값을 읽는다(LiveKit 과
        # 같은 원칙: 선언과 scheduling 판정이 한 값을 읽어야 「서버는 비동기인데 우리는 동기」가 안 생긴다).
        self._declared_behavior: dict[str, str | None] = {}
        self._declared_scheduling: dict[str, str | None] = {}
        # 진행 중인 NON_BLOCKING 도구 태스크. tool_call_cancellation 의 id 로 취소한다.
        self._inflight: dict[str, asyncio.Task[Any]] = {}
        self._scheduling_dropped_warned = False

        client_kwargs: dict[str, Any] = {}
        if api_key:
            client_kwargs["api_key"] = api_key
        if api_version:
            # 기본값은 v1beta. proactivity 등 일부 기능은 다른 버전을 요구할 수 있어 그대로 넘긴다.
            client_kwargs["http_options"] = {"api_version": api_version}
        self._client = genai.Client(**client_kwargs)
        self._trace_t0: float = time.monotonic()
        self._live_ctx: Any | None = None  # async context manager
        self._session: Any | None = None  # AsyncLiveSession
        from .._buffering_call import _BufferingCall as _BC

        self._call: CallSession | _BC | None = None
        self._sent_audio_chunks: int = 0
        self._pending_tool_call: Any | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        self._llm_span_ctx: Any | None = None
        self._llm_span: Any | None = None
        self._audio_remainder: bytes = b""  # 160B 미만 잔여 ulaw 버퍼
        self._hold_audio_chunks: list[bytes] | None = None
        self._first_audio_logged: bool = False

    def set_hold_audio(self, chunks: list[bytes]) -> None:
        """Tool 실행 중 재생할 hold audio 청크를 설정한다."""
        self._hold_audio_chunks = chunks

    def set_tool_registry(self, registry: ToolRegistry) -> None:
        """콜별로 fork된 ToolRegistry를 주입한다."""
        self._tools = registry

    def set_builtin_tools(self, tools: set[BuiltinTool]) -> None:
        """사용할 내장 도구 set을 지정."""
        self._builtin_tools = tools

    def set_recorder(self, recorder: AudioRecorder) -> None:
        """콜별로 생성된 AudioRecorder를 주입한다."""
        self._recorder = recorder

    def get_telemetry(self) -> dict[str, Any]:
        return {
            "sessionType": "gemini_realtime",
            "llm": {"provider": "gemini", "model": self._model},
            "stt": None,
            "tts": None,
            "voice": self._voice,
            "language": self._language,
            "greetingEnabled": self._greeting,
            "recordingEnabled": self._recorder is not None,
            "toolCount": 0,
            "mcpServerCount": 0,
            "builtinTools": [],
        }

    async def _open_live_session(self) -> Any:
        """Gemini Live 세션을 열어 핸들을 반환한다. 테스트 mocking 지점."""
        tool_schemas = self._build_tool_schemas()

        config: dict[str, Any] = {
            "response_modalities": ["AUDIO"],
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {"voice_name": self._voice},
                },
            },
            "input_audio_transcription": {},
            "output_audio_transcription": {},
        }

        if self._realtime_input_config:
            config["realtime_input_config"] = self._realtime_input_config

        # 모델마다 받는 키가 다르다(2026-09-29 실측): gemini-3.8-live-extended-thinking 은
        # thinking_level(LOW·MEDIUM·HIGH)이 없으면 연결을 거절하고, gemini-3.8-live 는
        # thinking_level 을 거절하고 thinking_budget 만 받는다. 그래서 값을 해석하지 않고 그대로 넘긴다.
        if self._thinking_config:
            config["thinking_config"] = self._thinking_config

        # 3.8 두 모델은 proactive audio 가 영구 ON 이라 False 를 보내면 거절되고, 3.1 은 미지원이다.
        # 값을 해석하지 않고 그대로 넘긴다 — 모델별 표는 docs/agent/quickstart.md.
        if self._proactive_audio is not None:
            config["proactivity"] = {"proactive_audio": self._proactive_audio}

        if self._system_prompt:
            config["system_instruction"] = types.Content(
                parts=[types.Part(text=self._system_prompt)],
            )

        if tool_schemas:
            config["tools"] = [{"function_declarations": tool_schemas}]

        log.debug("Gemini SDK final config: %s", config)
        log.debug(f"Gemini SDK tool count: {len(tool_schemas)}")

        self._live_ctx = self._client.aio.live.connect(
            model=self._model,
            config=config,
        )
        self._trace_t0 = time.monotonic()
        self._trace("connect", model=self._model, tool_behavior=self.effective_tool_behavior())
        return await self._live_ctx.__aenter__()

    def effective_tool_behavior(self) -> str | None:
        """이 세션의 함수 선언 기본 ``behavior``. 지정이 없으면 모델별 기본값. 도구별 값이 이를 덮는다."""
        if self._tool_behavior is not None:
            return self._tool_behavior
        return default_tool_behavior(self._model)

    def _behavior_for(self, name: str, requested: str | None, *, builtin: bool) -> str | None:
        """선언 하나에 실을 behavior 를 정한다.

        - extended-thinking 은 NON_BLOCKING 만 받으므로 무엇을 요청해도 NON_BLOCKING(요청이 BLOCKING 이면 경고).
        - 내장 도구(hang_up·collect_dtmf·send_dtmf·transfer_call)는 통화 제어라 항상 BLOCKING —
          NON_BLOCKING 이면 모델이 끊기·전환 중에도 말을 이어 가 음성이 잘린다.
        - 그 외는 도구별 요청 → 세션 기본값 순.
        """
        if is_extended_thinking_model(self._model):
            if requested == "BLOCKING":
                log.warning(
                    "tool %s requested BLOCKING but %s accepts NON_BLOCKING only — declaring NON_BLOCKING",
                    name,
                    self._model,
                )
            return "NON_BLOCKING"
        if builtin:
            return "BLOCKING" if self.effective_tool_behavior() else None
        return requested or self.effective_tool_behavior()

    def _scheduling_for(self, name: str, result_is_none: bool) -> str | None:
        """응답에 실을 ``scheduling``. NON_BLOCKING 선언에만 의미가 있고 BLOCKING 엔 서버가 무시한다.

        extended-thinking 은 scheduling 을 거절(1007 "Function response scheduling is not supported")하므로
        절대 보내지 않는다. 결과가 None(=모델에 전할 말이 없음)이고 따로 정한 값이 없으면 SILENT.
        """
        if self._declared_behavior.get(name) != "NON_BLOCKING":
            return None
        requested = self._declared_scheduling.get(name) or self._tool_response_scheduling
        if is_extended_thinking_model(self._model):
            if requested and not self._scheduling_dropped_warned:
                self._scheduling_dropped_warned = True
                log.warning("%s rejects FunctionResponse.scheduling; dropping scheduling=%s", self._model, requested)
            return None
        if requested:
            return requested
        return "SILENT" if result_is_none else None

    def _trace(self, event: str, **fields: Any) -> None:
        """``CLAWOPS_GEMINI_DEBUG=1`` 일 때만 서버·클라이언트 이벤트를 한 줄로 남긴다."""
        if not _TRACE_ENABLED:
            return
        elapsed_ms = int((time.monotonic() - self._trace_t0) * 1000)
        kv = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
        log.info("[gemini-trace] +%dms %s %s", elapsed_ms, event, kv)

    async def prewarm(self) -> None:
        """Gemini Live 세션 prewarm — CallSession 없이 호출 가능."""
        from .._buffering_call import _BufferingCall

        self._call = _BufferingCall()
        self._audio_remainder = b""
        self._first_audio_logged = False
        self._sent_audio_chunks = 0

        self._llm_span_ctx = llm_session_span(self._model, system="google", voice=self._voice)
        self._llm_span = self._llm_span_ctx.__enter__()

        try:
            self._session = await self._open_live_session()
        except Exception:
            if self._llm_span_ctx:
                import sys

                self._llm_span_ctx.__exit__(*sys.exc_info())
                self._llm_span_ctx = None
            raise
        log.info("Gemini Live SDK session connected (prewarm)")

        if self._greeting:
            await self._session.send_realtime_input(
                text="인사해 주세요.",
            )

        self._tasks.append(asyncio.create_task(self._receive_loop()))

    async def attach(self, call: CallSession) -> None:
        """prewarmed 세션에 실제 CallSession 부착 + 버퍼 flush."""
        from .._buffering_call import drain_into

        prev = self._call
        self._call = call
        flushed = await drain_into(prev, call)
        if flushed:
            self._first_audio_logged = True

    async def start(self, call: CallSession) -> None:
        """기존 호환 경로 — prewarm + attach wrapper."""
        await self.prewarm()
        await self.attach(call)

    async def feed_audio(self, audio: bytes, timestamp: int) -> None:
        if not self._session:
            return

        # G.711 ulaw 8kHz → PCM16 8kHz → PCM16 16kHz
        # inbound 녹음은 _agent.py on_audio 콜백에서 공통 처리한다.
        pcm8k = ulaw_to_pcm16(audio)
        pcm16k = resample_pcm16(pcm8k, from_rate=8000, to_rate=16000)

        self._sent_audio_chunks += 1

        try:
            await self._session.send_realtime_input(
                audio=types.Blob(data=pcm16k, mime_type="audio/pcm;rate=16000"),
            )
        except Exception as e:
            log.warning(f"Gemini audio send failed: {e}")

    async def feed_dtmf(self, digits: str) -> None:
        """DTMF digit을 LLM 컨텍스트에 주입하고 응답을 트리거한다."""
        if self._session:
            await self._session.send_realtime_input(
                text=f"[DTMF 입력: {digits}]",
            )

    # Gemini는 tool_call 후에도 오디오를 계속 보내므로, 이 시간 내 응답이 없으면 drain 완료로 간주
    _TOOL_DRAIN_TIMEOUT = 0.3

    async def _receive_loop(self) -> None:
        if not self._session:
            return
        try:
            # SDK의 receive()는 turn_complete 시 iterator가 종료되므로
            # 매 턴마다 다시 호출해야 한다.
            while self._session:
                self._pending_tool_call = None
                it = self._session.receive().__aiter__()
                try:
                    while True:
                        if self._pending_tool_call:
                            response = await asyncio.wait_for(it.__anext__(), timeout=self._TOOL_DRAIN_TIMEOUT)
                        else:
                            response = await it.__anext__()
                        await self._handle_response(response)
                except (StopAsyncIteration, asyncio.TimeoutError):
                    pass
                if self._pending_tool_call:
                    await self._handle_tool_calls(self._pending_tool_call)
                    self._pending_tool_call = None
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error(f"Gemini receive error: {e}")
        finally:
            await self._cleanup()

    async def _handle_response(self, response: Any) -> None:
        if not self._call:
            return

        if _TRACE_ENABLED:
            self._trace_response(response)

        server_content = getattr(response, "server_content", None)
        if server_content:
            # ── 오디오 응답 ──
            model_turn = getattr(server_content, "model_turn", None)
            if model_turn:
                for part in getattr(model_turn, "parts", []):
                    inline = getattr(part, "inline_data", None)
                    if inline and "audio" in (getattr(inline, "mime_type", "") or ""):
                        await self._handle_audio_data(inline.data)

            # 생성 완료 — 오디오는 이 시점에 다 왔다. turn_complete 는 모델이 「재생이 끝났다」고 믿는
            # 시점까지 미뤄지고(모델은 실시간 재생을 가정) 3.8 에선 안 오기도 하므로 여기서도 flush 한다.
            if getattr(server_content, "generation_complete", False):
                log.debug("Gemini generation complete")
                await self._flush_audio_remainder()

            # 턴 완료
            if getattr(server_content, "turn_complete", False):
                log.debug("Gemini turn complete")
                await self._flush_audio_remainder()

            # barge-in (인터럽트)
            if getattr(server_content, "interrupted", False):
                log.info("Gemini: barge-in detected")
                await self._call.clear_audio()
                self._sent_audio_chunks = 0
                self._audio_remainder = b""

            # ── 입력 트랜스크립트 ──
            input_transcription = getattr(server_content, "input_transcription", None)
            if input_transcription:
                text = getattr(input_transcription, "text", "")
                if text:
                    log.debug(f"[Transcript] user: {text}")
                    await self._call._emit("transcript", "user", text)

            # ── 출력 트랜스크립트 ──
            output_transcription = getattr(server_content, "output_transcription", None)
            if output_transcription:
                text = getattr(output_transcription, "text", "")
                if text:
                    log.debug(f"[Transcript] assistant: {text}")
                    await self._call._emit("transcript", "assistant", text)

        # ── Tool call ──
        tool_call = getattr(response, "tool_call", None)
        if tool_call:
            blocking = self._dispatch_tool_call(tool_call)
            if blocking is not None:
                self._pending_tool_call = blocking

        # ── Tool call cancellation ──
        tool_cancellation = getattr(response, "tool_call_cancellation", None)
        if tool_cancellation:
            ids = list(getattr(tool_cancellation, "ids", None) or [])
            log.info(f"Gemini tool call cancelled: {ids}")
            self._pending_tool_call = None
            self._cancel_inflight(ids)

    def _dispatch_tool_call(self, tool_call: Any) -> Any | None:
        """NON_BLOCKING 호출은 즉시 태스크로 띄우고, BLOCKING 호출만 묶어 돌려준다(drain 뒤 실행).

        NON_BLOCKING 은 서버가 결과를 기다리지 않고 모델이 계속 말하므로 수신 루프를 막으면 안 되고,
        hold audio 도 틀지 않는다(모델 음성과 겹친다).
        """
        function_calls = list(getattr(tool_call, "function_calls", None) or [])
        blocking_calls = []
        for fc in function_calls:
            name = getattr(fc, "name", "")
            if self._declared_behavior.get(name) == "NON_BLOCKING":
                key = getattr(fc, "id", None) or f"{name}@{id(fc)}"
                task = asyncio.create_task(self._run_async_tool(key, fc))
                self._inflight[key] = task
                self._tasks.append(task)
            else:
                blocking_calls.append(fc)
        if not blocking_calls:
            return None
        if len(blocking_calls) == len(function_calls):
            return tool_call
        return types.LiveServerToolCall(function_calls=blocking_calls)

    def _cancel_inflight(self, ids: list[str]) -> None:
        for fc_id in ids:
            task = self._inflight.pop(fc_id, None)
            if task and not task.done():
                task.cancel()

    async def _run_async_tool(self, key: str, fc: Any) -> None:
        """NON_BLOCKING 도구 하나를 실행하고 끝나는 즉시 응답을 보낸다. 취소되면 응답을 보내지 않는다."""
        try:
            resp = await self._execute_function_call(fc)
            if resp is not None and self._session and self._inflight.get(key) is asyncio.current_task():
                self._trace("tool_response", names=resp.name, scheduling=resp.scheduling, mode="non_blocking")
                await self._session.send_tool_response(function_responses=[resp])
        except asyncio.CancelledError:
            log.info("NON_BLOCKING tool %s cancelled by server", getattr(fc, "name", ""))
            raise
        except Exception as e:
            log.error(f"NON_BLOCKING tool task failed: {e}")
        finally:
            if self._inflight.get(key) is asyncio.current_task():
                self._inflight.pop(key, None)

    def _trace_response(self, response: Any) -> None:
        sc = getattr(response, "server_content", None)
        if sc:
            audio_bytes = 0
            model_turn = getattr(sc, "model_turn", None)
            if model_turn:
                for part in getattr(model_turn, "parts", None) or []:
                    inline = getattr(part, "inline_data", None)
                    if inline and getattr(inline, "data", None):
                        audio_bytes += len(inline.data)
            it = getattr(sc, "input_transcription", None)
            ot = getattr(sc, "output_transcription", None)
            self._trace(
                "server_content",
                audio_bytes=audio_bytes or None,
                turn_complete=True if getattr(sc, "turn_complete", False) else None,
                turn_complete_reason=getattr(sc, "turn_complete_reason", None),
                generation_complete=True if getattr(sc, "generation_complete", False) else None,
                waiting_for_input=True if getattr(sc, "waiting_for_input", False) else None,
                interrupted=True if getattr(sc, "interrupted", False) else None,
                input_transcript=repr(it.text) if it and getattr(it, "text", None) else None,
                output_transcript=repr(ot.text) if ot and getattr(ot, "text", None) else None,
            )
        tc = getattr(response, "tool_call", None)
        if tc:
            calls = [
                f"{getattr(fc, 'name', '')}#{getattr(fc, 'id', '')}" for fc in getattr(tc, "function_calls", None) or []
            ]
            self._trace("tool_call", calls=",".join(calls))
        cancel = getattr(response, "tool_call_cancellation", None)
        if cancel:
            self._trace("tool_call_cancellation", ids=",".join(getattr(cancel, "ids", None) or []))
        if getattr(response, "go_away", None):
            self._trace("go_away", time_left=getattr(response.go_away, "time_left", None))

    async def _handle_audio_data(self, audio_data: bytes) -> None:
        """PCM16 24kHz raw bytes → G.711 ulaw 8kHz 변환 후 전송."""
        if not self._call:
            return

        # PCM16 24kHz → PCM16 8kHz → ulaw
        pcm8k = resample_pcm16(audio_data, from_rate=24000, to_rate=8000)
        ulaw = pcm16_to_ulaw(pcm8k)

        # 160B 프레임 정렬
        ulaw = self._audio_remainder + ulaw
        chunk_size = 160
        full_end = (len(ulaw) // chunk_size) * chunk_size
        for off in range(0, full_end, chunk_size):
            if not self._first_audio_logged:
                from .._buffering_call import log_first_realtime_audio

                log_first_realtime_audio(self._call)
                self._first_audio_logged = True
            await self._call.send_audio(ulaw[off : off + chunk_size])
        self._audio_remainder = ulaw[full_end:]

    async def _flush_audio_remainder(self) -> None:
        """잔여 오디오를 silence 패딩하여 flush."""
        if self._audio_remainder and self._call:
            padded = self._audio_remainder + b"\xff" * (160 - len(self._audio_remainder))
            await self._call.send_audio(padded)
            self._audio_remainder = b""

    class _HangUp(Exception):
        """hang_up 내장 도구가 통화를 끊었다 — 더 보낼 응답이 없다."""

    async def _execute_function_call(self, fc: Any) -> types.FunctionResponse | None:
        """함수 호출 하나를 실행해 FunctionResponse 로 만든다. hang_up 이면 :class:`_HangUp`.

        서버가 id 를 주지 않은 호출에는 id 를 돌려보내지 않는다(우리가 지어낸 id 는 서버가 모른다).
        """
        func_name = getattr(fc, "name", "")
        fc_id = getattr(fc, "id", None) or None
        args = getattr(fc, "args", None) or {}
        log.info(f"Tool call: {func_name}({args})")

        result: Any
        if func_name in BUILTIN_TOOL_NAMES and self._call:
            self._call.metrics.record_tool_call()
            result = await execute_builtin_tool(func_name, args, self._call)
            if result == "":  # hang_up
                raise GeminiRealtime._HangUp()
            if result is None:
                return None
        else:
            if self._call:
                self._call.metrics.record_tool_call()
            source = "mcp" if func_name in self._tools._mcp_tools else "local"
            with tool_call_span(func_name, source):
                try:
                    result = await self._tools.call(func_name, args)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.error(f"Tool call failed: {func_name}: {e}")
                    if self._call:
                        self._call.metrics.record_tool_error(e)
                    result = f"Error: {e}"

        # None=모델에 전할 말이 없다. NON_BLOCKING 이면 SILENT 로 보내 모델이 쓸데없이 답하지 않게 한다.
        payload = {"result": "ok"} if result is None else {"result": str(result)}
        return types.FunctionResponse(
            id=fc_id,
            name=func_name,
            response=payload,
            scheduling=self._scheduling_for(func_name, result is None),
        )

    async def _handle_tool_calls(self, tool_call: Any) -> None:
        """BLOCKING 호출 묶음을 실행한다. 모델이 결과를 기다리므로 그동안 hold audio 를 튼다."""
        function_calls = getattr(tool_call, "function_calls", [])
        responses: list[types.FunctionResponse] = []

        player = (
            HoldAudioPlayer(self._call, self._hold_audio_chunks) if self._hold_audio_chunks and self._call else None
        )
        if player:
            await player.start()

        try:
            for fc in function_calls:
                try:
                    resp = await self._execute_function_call(fc)
                except GeminiRealtime._HangUp:
                    return
                if resp is not None:
                    responses.append(resp)
        finally:
            if player:
                await player.stop()

        if responses:
            self._trace("tool_response", names=",".join(r.name or "" for r in responses), mode="blocking")
            await self._session.send_tool_response(
                function_responses=responses,
            )

    def _build_tool_schemas(self) -> list[dict[str, Any]]:
        """ToolRegistry의 스키마를 Gemini functionDeclarations 형식으로 변환.

        ``behavior`` 는 세션의 :meth:`effective_tool_behavior` 를 모든 선언(커스텀·MCP·내장)에
        같이 싣는다. 값이 None 이면 키를 넣지 않는다(서버 기본값).
        """
        openai_tools = self._tools.to_openai_tools()
        declarations = []
        for tool in openai_tools:
            params = tool.get("parameters", {"type": "object", "properties": {}})
            params = _sanitize_schema_for_gemini(params)
            declarations.append(
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": params,
                }
            )
        builtin_decls = get_builtin_tool_schemas(self._builtin_tools, fmt="gemini")

        self._declared_behavior.clear()
        self._declared_scheduling.clear()
        for decl in declarations:
            tool = self._tools._tools.get(decl["name"])  # MCP 도구는 여기 없다 → 세션 기본값
            behavior = self._behavior_for(decl["name"], tool.behavior if tool else None, builtin=False)
            if behavior:
                decl["behavior"] = behavior
            self._declared_behavior[decl["name"]] = behavior
            self._declared_scheduling[decl["name"]] = tool.scheduling if tool else None
        for decl in builtin_decls:
            behavior = self._behavior_for(decl["name"], None, builtin=True)
            if behavior:
                decl["behavior"] = behavior
            self._declared_behavior[decl["name"]] = behavior
            self._declared_scheduling[decl["name"]] = None
        declarations.extend(builtin_decls)
        return declarations

    async def stop(self) -> None:
        # cancel 후 gather 로 수거 — 안 하면 receive loop 의 예외가 retrieve 되지 않아
        # "Task exception was never retrieved" 경고가 발생한다 (특히 prewarm 창이 길 때).
        self._cancel_inflight(list(self._inflight))
        current = asyncio.current_task()
        pending = [t for t in self._tasks if t is not current]
        for task in pending:
            if not task.done():
                task.cancel()
        self._tasks.clear()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await self._cleanup()

    async def _cleanup(self) -> None:
        if self._live_ctx:
            try:
                await asyncio.wait_for(
                    self._live_ctx.__aexit__(None, None, None),
                    timeout=2.0,
                )
            except (asyncio.TimeoutError, Exception) as e:
                log.debug(f"Gemini SDK cleanup: {e}")
            self._live_ctx = None
            self._session = None
        if self._llm_span_ctx:
            import sys

            exc_info = sys.exc_info()
            self._llm_span_ctx.__exit__(*exc_info)
            self._llm_span_ctx = None
            self._llm_span = None
