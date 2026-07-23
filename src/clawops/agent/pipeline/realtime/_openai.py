"""OpenAI Realtime API 세션.

Session Protocol을 구현하며, OpenAI Realtime WebSocket API를 사용한다.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any

try:
    from openai import AsyncOpenAI
    from openai.types.realtime.realtime_audio_input_turn_detection_param import (
        RealtimeAudioInputTurnDetectionParam,
    )
    from openai.resources.realtime.realtime import AsyncRealtimeConnection

    _HAS_OPENAI = True
except ImportError:
    AsyncOpenAI = None  # type: ignore[assignment,misc]
    RealtimeAudioInputTurnDetectionParam = None  # type: ignore[assignment,misc]
    AsyncRealtimeConnection = None  # type: ignore[assignment,misc]
    _HAS_OPENAI = False

from ..._builtin_tools import BuiltinTool
from ..._hold_audio import HoldAudioPlayer
from ..._recorder import AudioRecorder
from ..._session import CallSession
from ..._tool import ToolRegistry
from ...tracing._spans import tool_call_span, llm_session_span
from .._builtin_tool_schemas import get_builtin_tool_schemas, execute_builtin_tool, BUILTIN_TOOL_NAMES

log = logging.getLogger("clawops.agent")


@dataclass
class _PlaybackState:
    """현재 재생 중인 응답의 상태. 하나의 assistant 응답에 대한 모든 정보를 담는다."""

    item_id: str  # OpenAI conversation item ID
    start_ts: int  # 첫 delta 수신 시점의 media timestamp
    sent_chunks: int = 0  # 플랫폼으로 전송된 오디오 청크 수 (각 20ms)
    generating: bool = True  # OpenAI가 아직 오디오를 생성 중인지
    audio_remainder: bytes = b""  # 160B 미만 잔여 오디오 버퍼

    @property
    def total_audio_ms(self) -> int:
        return self.sent_chunks * 20

    def elapsed_ms(self, current_ts: int) -> int:
        """사용자가 실제 들은 오디오 길이를 추정한다."""
        return max(0, current_ts - self.start_ts)


@dataclass
class OpenAIRealtimeConfig:
    """OpenAIRealtime 내부 설정. 하위 호환용으로 유지."""

    system_prompt: str
    openai_api_key: str
    voice: str = "marin"
    model: str = "gpt-realtime-2"
    language: str = "ko"
    turn_detection: RealtimeAudioInputTurnDetectionParam | None = None
    greeting: bool = True


class OpenAIRealtime:
    """OpenAI Realtime API 기반 음성 세션.

    Session Protocol을 구현한다. ``gpt-realtime-2``, ``gpt-4o-mini-realtime``
    등 OpenAI의 realtime 모델을 사용한다.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        system_prompt: str = "",
        model: str = "gpt-realtime-2",
        voice: str = "marin",
        language: str = "ko",
        turn_detection: RealtimeAudioInputTurnDetectionParam | None = None,
        greeting: bool = True,
        tool_registry: ToolRegistry | None = None,
        recorder: AudioRecorder | None = None,
    ) -> None:
        if not _HAS_OPENAI:
            raise ImportError("openai is required for OpenAIRealtime. Install it with: pip install clawops[openai]")
        if api_key is None:
            api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OpenAI API key is required. Set OPENAI_API_KEY env var or pass api_key argument.")
        if turn_detection is None:
            turn_detection = {
                "type": "semantic_vad",
                "eagerness": "low",
                "create_response": True,
                "interrupt_response": True,
            }
        self._config = OpenAIRealtimeConfig(
            system_prompt=system_prompt,
            openai_api_key=api_key,
            voice=voice,
            model=model,
            language=language,
            turn_detection=turn_detection,
            greeting=greeting,
        )
        self._tools = tool_registry or ToolRegistry()
        self._builtin_tools: set[BuiltinTool] | None = None
        self._client: AsyncOpenAI | None = None
        self._connection: AsyncRealtimeConnection | None = None
        from .._buffering_call import _BufferingCall as _BC

        self._call: CallSession | _BC | None = None
        self._playback: _PlaybackState | None = None  # 현재 재생 중인 응답 상태
        self._latest_media_ts: int = 0
        self._recorder = recorder
        self._tasks: list[asyncio.Task[Any]] = []
        self._pending_tool_tasks: set[asyncio.Task[Any]] = set()
        self._hold_audio_chunks: list[bytes] | None = None
        self._llm_span_ctx: Any | None = None
        self._llm_span: Any | None = None
        # PREWARM-T first-audio 마커를 한 번만 emit 하기 위한 가드.
        # drain_into 가 prebuffer 를 flush 한 케이스 / 비어있던 케이스 모두 한 번씩.
        self._first_audio_logged: bool = False
        # 마지막 session.update 로 LLM 에 알린 tool 이름들. attach() 가 이 값과
        # 현재 registry 를 비교해 달라졌을 때만(=MCP 도구 등 뒤늦게 붙은 경우)
        # 도구를 재전송한다.
        self._sent_tool_names: list[str] | None = None

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
            "sessionType": "openai_realtime",
            "llm": {"provider": "openai", "model": self._config.model},
            "stt": None,
            "tts": None,
            "voice": self._config.voice,
            "language": self._config.language,
            "greetingEnabled": self._config.greeting,
            "recordingEnabled": self._recorder is not None,
            "toolCount": 0,
            "mcpServerCount": 0,
            "builtinTools": [],
        }

    async def _open_connection(self) -> AsyncRealtimeConnection:
        """OpenAI Realtime WS 를 열어 connection 핸들을 반환한다.

        테스트에서 mocking 하기 위해 별도 메서드로 추출.
        """
        self._client = AsyncOpenAI(api_key=self._config.openai_api_key)
        manager = self._client.realtime.connect(model=self._config.model)
        return await manager.enter()

    async def prewarm(self) -> None:
        """LLM WS 연결 + session.update + (옵션) response.create 만 수행한다.

        CallSession 없이 호출 가능. attach() 가 호출되기 전까지 도착하는
        audio delta 는 _BufferingCall 의 메모리 버퍼에 누적된다.
        """
        from .._buffering_call import _BufferingCall  # local import: 순환 회피

        self._call = _BufferingCall()
        self._latest_media_ts = 0
        self._first_audio_logged = False

        # Start LLM session span
        self._llm_span_ctx = llm_session_span(self._config.model, voice=self._config.voice)
        self._llm_span = self._llm_span_ctx.__enter__()

        self._connection = await self._open_connection()
        log.info("OpenAI Realtime connected (prewarm)")

        tool_schemas = self._current_tool_schemas()
        self._sent_tool_names = [str(t.get("name", "")) for t in tool_schemas]

        await self._connection.session.update(
            session={
                "type": "realtime",
                "output_modalities": ["audio"],
                "instructions": self._config.system_prompt,
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcmu"},
                        "noise_reduction": {"type": "near_field"},
                        "transcription": {
                            "model": "gpt-4o-transcribe",
                            "language": self._config.language,
                        },
                        "turn_detection": self._config.turn_detection,
                    },
                    "output": {
                        "format": {"type": "audio/pcmu"},
                        "voice": self._config.voice,
                    },
                },
                "tools": tool_schemas,
                "tracing": "auto",
            }
        )

        if self._config.greeting:
            await self._connection.response.create()

        self._tasks.append(asyncio.create_task(self._receive_loop()))

    def _current_tool_schemas(self) -> list[dict[str, Any]]:
        """현재 registry + 내장 도구의 realtime 포맷 스키마."""
        schemas = self._tools.to_openai_tools()
        schemas.extend(get_builtin_tool_schemas(self._builtin_tools, fmt="realtime"))
        return schemas

    async def _resync_tools(self) -> None:
        """prewarm 이후 도구가 바뀌었으면 session.update 로 재전송한다.

        MCP 도구는 통화 시작 시점에 registry 에 붙으므로 prewarm 시점의 스키마에는
        없다. 여기서 맞춰주지 않으면 발신 통화에서 MCP 도구를 영영 못 쓴다.
        """
        if self._connection is None:
            return
        tool_schemas = self._current_tool_schemas()
        names = [str(t.get("name", "")) for t in tool_schemas]
        if names == self._sent_tool_names:
            return
        try:
            await self._connection.session.update(
                session={"type": "realtime", "tools": tool_schemas}
            )
            self._sent_tool_names = names
            log.info("Tool schema resynced after prewarm: %s", names)
        except Exception as e:
            # 도구 재동기화 실패가 통화를 끊게 두지 않는다.
            log.warning("Tool resync failed: %s", e)

    async def attach(self, call: CallSession) -> None:
        """prewarmed 세션에 실제 CallSession 부착 + 버퍼 flush.

        prewarm() 이 선행되어 있어야 한다. start() 는 prewarm+attach wrapper.
        """
        from .._buffering_call import drain_into

        await self._resync_tools()

        prev = self._call
        self._call = call
        flushed = await drain_into(prev, call)
        if flushed:
            self._first_audio_logged = True

    async def start(self, call: CallSession) -> None:
        """기존 호환 경로 — prewarm + attach 의 thin wrapper."""
        await self.prewarm()
        await self.attach(call)

    async def feed_audio(self, audio: bytes, timestamp: int) -> None:
        self._latest_media_ts = timestamp
        if self._connection:
            await self._connection.input_audio_buffer.append(
                audio=base64.b64encode(audio).decode(),
            )

    async def feed_dtmf(self, digits: str) -> None:
        """DTMF digit을 LLM 컨텍스트에 주입하고 응답을 트리거한다."""
        if self._connection:
            await self._connection.conversation.item.create(
                item={
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": f"[DTMF 입력: {digits}]"}],
                }
            )
            await self._connection.response.create()

    async def _receive_loop(self) -> None:
        if not self._connection:
            return
        try:
            async for event in self._connection:
                await self._handle_event(event)
        except Exception as e:
            log.error(f"Realtime receive error: {e}")
        finally:
            await self._cleanup()

    async def _handle_event(self, event: Any) -> None:
        if not self._call:
            return
        event_type = event.type

        if event_type == "response.output_audio.delta":
            if self._playback is None:
                self._playback = _PlaybackState(
                    item_id=event.item_id or "",
                    start_ts=self._latest_media_ts,
                )
                self._diag_delta_count = 0
                self._diag_last_delta_time = asyncio.get_event_loop().time()
            elif event.item_id:
                self._playback.item_id = event.item_id

            pb = self._playback
            ulaw = base64.b64decode(event.delta)
            self._diag_delta_count = getattr(self, "_diag_delta_count", 0) + 1
            now = asyncio.get_event_loop().time()
            if self._diag_delta_count % 50 == 0:
                elapsed = now - getattr(self, "_diag_last_delta_time", now)
                log.info(
                    f"[OAI-DIAG] delta#{self._diag_delta_count} "
                    f"last50in={elapsed * 1000:.0f}ms "
                    f"avg={elapsed * 1000 / 50:.1f}ms/delta "
                    f"size={len(ulaw)}B "
                    f"totalChunks={pb.sent_chunks}"
                )
                self._diag_last_delta_time = now
            ulaw = pb.audio_remainder + ulaw
            chunk_size = 160
            full_end = (len(ulaw) // chunk_size) * chunk_size
            for off in range(0, full_end, chunk_size):
                if not self._first_audio_logged:
                    from .._buffering_call import log_first_realtime_audio

                    log_first_realtime_audio(self._call)
                    self._first_audio_logged = True
                await self._call.send_audio(ulaw[off : off + chunk_size])
                pb.sent_chunks += 1
            pb.audio_remainder = ulaw[full_end:]

        elif event_type == "response.output_audio.done":
            if self._playback:
                self._playback.generating = False
                if self._playback.audio_remainder:
                    padded = self._playback.audio_remainder + b"\xff" * (160 - len(self._playback.audio_remainder))
                    await self._call.send_audio(padded)
                    self._playback.sent_chunks += 1
                    self._playback.audio_remainder = b""

        elif event_type == "input_audio_buffer.speech_started":
            await self._handle_truncation()

        elif event_type == "response.output_item.done":
            item = event.item
            if item and item.type == "function_call":
                task = asyncio.create_task(self._handle_tool_call(item))
                self._pending_tool_tasks.add(task)
                task.add_done_callback(self._pending_tool_tasks.discard)
            # _playback은 여기서 리셋하지 않는다.
            # 큐에 재생 대기 중인 오디오가 있을 수 있고,
            # 인터럽트 시 truncate하려면 item_id가 필요하다.

        elif event_type == "conversation.item.input_audio_transcription.completed":
            transcript = event.transcript or ""
            log.debug(f"[Transcript] user: {transcript}")
            await self._call._emit("transcript", "user", transcript)

        elif event_type == "response.output_audio_transcript.done":
            transcript = event.transcript or ""
            log.debug(f"[Transcript] assistant: {transcript}")
            await self._call._emit("transcript", "assistant", transcript)

        elif event_type == "error":
            log.error(f"OpenAI error: {event.error}")

    async def _handle_tool_call(self, item: Any) -> None:
        func_name = item.name or ""
        call_id = item.call_id or ""
        args = json.loads(item.arguments or "{}")
        log.info(f"Tool call: {func_name}({args})")

        try:
            # Builtin tool 처리
            if func_name in BUILTIN_TOOL_NAMES and self._call:
                if self._call:
                    self._call.metrics.record_tool_call()
                result = await execute_builtin_tool(func_name, args, self._call)
                if result == "":  # hang_up
                    return
                if result is not None and self._connection:
                    await self._connection.conversation.item.create(
                        item={
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": result,
                        }
                    )
                    await self._connection.response.create()
                return

            if not self._connection:
                return

            # Unknown tool 처리 — 모델이 복구할 수 있도록 에러 결과를 반환
            if func_name not in self._tools:
                log.error(f"Unknown tool: {func_name}")
                await self._connection.conversation.item.create(
                    item={
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps({"error": f"Unknown tool: {func_name}"}),
                    }
                )
                await self._connection.response.create()
                return

            # Custom / MCP tool 처리
            if self._call:
                self._call.metrics.record_tool_call()
            source = "mcp" if func_name in self._tools._mcp_tools else "local"

            player = (
                HoldAudioPlayer(self._call, self._hold_audio_chunks) if self._hold_audio_chunks and self._call else None
            )
            if player:
                await player.start()

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
                finally:
                    if player:
                        await player.stop()

            log.debug(f"Tool result: {func_name} -> {str(result)[:200]}")

            result_str = str(result)
            await self._connection.conversation.item.create(
                item={
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": result_str,
                }
            )
            log.info(f"[ToolResult] {func_name} call_id={call_id} len={len(result_str)}")
            await self._connection.response.create()

        except asyncio.CancelledError:
            log.info(f"Tool call cancelled (user interrupted): {func_name}")
            return

    async def _handle_truncation(self) -> None:
        # 진행 중인 tool call 취소
        for task in list(self._pending_tool_tasks):
            if not task.done():
                task.cancel()
        self._pending_tool_tasks.clear()

        # 큐에 남아있는 오디오를 항상 비운다
        if self._call:
            await self._call.clear_audio()

        pb = self._playback
        if pb is None:
            return

        # interrupt_response=True이므로 서버가 자동으로 응답을 취소한다.
        # response.cancel()을 중복 호출하면 서버 상태가 꼬일 수 있으므로 생략.
        # conversation.item.truncate()는 오디오와 transcript를 모두 잘라내어
        # 대화 맥락을 손실시키므로 호출하지 않는다.
        log.info(
            f"[Interrupt] item={pb.item_id} played={pb.elapsed_ms(self._latest_media_ts)}ms total={pb.total_audio_ms}ms"
        )

        self._playback = None

    async def stop(self) -> None:
        # 1) 연결 닫기 → receive loop의 async for가 자연 종료
        if self._connection:
            try:
                await asyncio.wait_for(self._connection.close(), timeout=2.0)
            except (asyncio.TimeoutError, Exception) as e:
                log.warning(f"OpenAI Realtime connection close error: {e}")
            self._connection = None
        # 2) 남은 태스크 정리 — cancel 후 gather 로 수거한다.
        #    cancel 만 하고 await 하지 않으면 receive loop 등의 예외가 retrieve 되지 않아
        #    "Task exception was never retrieved" 경고가 발생한다 (특히 prewarm 창이 길 때).
        #    현재 실행 중인 task 가 목록에 있으면 self-await 가 되므로 제외한다.
        current = asyncio.current_task()
        pending = [t for t in (*self._tasks, *self._pending_tool_tasks) if t is not current]
        for task in pending:
            if not task.done():
                task.cancel()
        self._tasks.clear()
        self._pending_tool_tasks.clear()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        # 3) LLM span 종료
        self._close_llm_span()

    async def _cleanup(self) -> None:
        if self._connection:
            try:
                await asyncio.wait_for(self._connection.close(), timeout=2.0)
            except (asyncio.TimeoutError, Exception) as e:
                log.warning(f"OpenAI Realtime connection cleanup error: {e}")
            self._connection = None
        self._close_llm_span()

    def _close_llm_span(self) -> None:
        if self._llm_span_ctx:
            exc_info = sys.exc_info()
            self._llm_span_ctx.__exit__(*exc_info)
            self._llm_span_ctx = None
            self._llm_span = None
