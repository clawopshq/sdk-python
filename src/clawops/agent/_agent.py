"""ClawOpsAgent: 메인 진입점.

Control WS 연결, per-call Media WS 생성, Session 관리를 조합한다.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import signal
import time
from typing import Any, Awaitable, Callable, Literal, TypedDict

from .._exceptions import AgentError
from ._builtin_tools import BuiltinTool, resolve_builtin_tools
from ._control_ws import ControlWebSocket
from ._hold_audio import load_hold_audio
from ._audio import apply_ulaw_gain, ulaw_to_pcm16
from .mcp._client import MCPClient
from ._media_ws import MediaWebSocket
from ._session import CallSession
from ._recorder import AudioRecorder
from ._telemetry import get_sdk_info
from ._tool import ToolRegistry
from .pipeline import Session
from .tracing import TracingConfig
from .tracing._spans import call_span, mcp_connect_span, setup_tracing

log = logging.getLogger("clawops.agent")


class ToolConfig(TypedDict, total=False):
    """Tool 실행 관련 설정."""

    hold_audio: bool | str | bytes
    """Tool 실행 중 재생할 hold audio. True=기본 차임, str=wav 파일 경로, bytes=raw ulaw."""


class ClawOpsAgent:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        account_id: str | None = None,
        base_url: str | None = None,
        from_: str,
        session: Session,
        mcp_servers: list[Any] | None = None,
        recording: bool = False,
        recording_path: str = "./recordings",
        tracing: TracingConfig | None = None,
        builtin_tools: BuiltinTool | list[BuiltinTool] = BuiltinTool.ALL,
        passive_dtmf_debounce_ms: int = 500,
        tool_config: ToolConfig | None = None,
        rx_gain: float = 1.0,
        tx_gain: float = 1.0,
        prewarm_enabled: bool = True,
        machine_detection: Literal["Enable", "Hangup"] | None = None,
    ) -> None:
        if api_key is None:
            api_key = os.environ.get("CLAWOPS_API_KEY")
        if api_key is None:
            raise AgentError("api_key를 지정하거나 CLAWOPS_API_KEY 환경변수를 설정하세요.")

        if account_id is None:
            account_id = os.environ.get("CLAWOPS_ACCOUNT_ID")
        if account_id is None:
            raise AgentError("account_id를 지정하거나 CLAWOPS_ACCOUNT_ID 환경변수를 설정하세요.")

        if base_url is None:
            base_url = os.environ.get("CLAWOPS_BASE_URL", "https://api.claw-ops.com")

        self._api_key = api_key
        self._account_id = account_id
        self._base_url = base_url
        self._from_number = from_

        self._session = session
        self._mcp_servers = mcp_servers or []
        self._recording = recording
        self._recording_path = recording_path
        self._tracing = tracing
        self._rx_gain = self._validate_gain("rx_gain", rx_gain)
        self._tx_gain = self._validate_gain("tx_gain", tx_gain)
        self._prewarm_enabled = prewarm_enabled
        # 모든 발신에 적용되는 AMD default. call() 인자로 호출별 override 가능.
        self._machine_detection: Literal["Enable", "Hangup"] | None = machine_detection

        if self._tracing is not None:
            setup_tracing(self._tracing)

        _hold_audio = tool_config.get("hold_audio", False) if tool_config else False
        self._hold_audio_chunks: list[bytes] | None = load_hold_audio(_hold_audio) if _hold_audio else None

        self._builtin_tools = resolve_builtin_tools(builtin_tools)
        self._passive_dtmf_debounce_ms = passive_dtmf_debounce_ms
        self._call_sessions: dict[str, Any] = {}  # call_id → pipeline Session (feed_dtmf용)

        self._tool_registry = ToolRegistry()
        self._event_handlers: dict[str, list[Callable[..., Awaitable[None]]]] = {}
        self._active_sessions: dict[str, CallSession] = {}
        self._control_ws: ControlWebSocket | None = None
        self._control_ws_task: asyncio.Task[Any] | None = None
        # prewarm 추적 — outbound_ready 수신 시 session.prewarm() 을 백그라운드 task 로
        # 시작하고, _start_call_session 이 await + attach() 로 부착한다.
        self._prewarm_tasks: dict[str, asyncio.Task[Any]] = {}
        self._prewarm_failed: set[str] = set()
        # attach 완료된 callId — 이후의 stop() 은 정상 종료 경로가 책임진다.
        # cleanup 이 attached 통화에 중복 stop() 하지 않도록 가드한다.
        self._prewarm_attached: set[str] = set()

    @staticmethod
    def _validate_gain(name: str, gain: float) -> float:
        value = float(gain)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name}={gain!r} must be a finite number >= 0")
        return value

    def tool(self, fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
        return self._tool_registry.register(fn)

    def on(self, event: str) -> Callable:
        def decorator(fn: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
            self._event_handlers.setdefault(event, []).append(fn)
            return fn

        return decorator

    async def connect(self) -> None:
        """Control WS에 연결한다. 블로킹하지 않는다."""
        if self._control_ws is not None:
            return
        # 세션 프로세스 수명 셋업 (있으면). control WS task + 모든 통화 task 의 공통
        # 조상인 이 시점(root task)에서 돌아야 하는 준비 작업 — 예: LiveKitSession 이
        # HTTP 플러그인용 http_context 를 여기서 연다.
        if hasattr(self._session, "session_setup"):
            await self._session.session_setup()
        await self._ensure_control_ws()
        log.info(f"ClawOpsAgent connected on {self._from_number}")

    async def serve(self) -> None:
        """인바운드 서버 모드: SIGINT/SIGTERM까지 대기 후 자동으로 disconnect()를 호출한다.

        connect()가 호출되지 않은 상태면 자동으로 connect()를 먼저 수행한다.
        """
        await self.connect()
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)
        try:
            await stop_event.wait()
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
            await self.disconnect()

    async def disconnect(self) -> None:
        """Control WS 닫기 + 활성 세션 정리."""
        if self._control_ws:
            await self._control_ws.close()
            self._control_ws = None
        if self._control_ws_task and not self._control_ws_task.done():
            self._control_ws_task.cancel()
            self._control_ws_task = None
        self._active_sessions.clear()
        self._call_sessions.clear()
        # session_setup 의 짝 — 같은 root task 에서 정리한다 (예: http_context 닫기).
        if hasattr(self._session, "session_teardown"):
            await self._session.session_teardown()
        log.info("ClawOpsAgent disconnected")

    async def _ensure_control_ws(self) -> None:
        """Control WS가 없으면 연결한다."""
        if self._control_ws is not None:
            return
        self._control_ws = ControlWebSocket(
            base_url=self._base_url,
            api_key=self._api_key,
            account_id=self._account_id,
            number=self._from_number,
            on_call_incoming=self._handle_incoming,
            on_call_ended=self._handle_ended,
            on_call_outbound_ready=self._handle_outbound_ready,
            on_call_ringing=self._handle_ringing,
            on_call_failed=self._handle_failed,
        )
        self._control_ws_task = asyncio.create_task(self._control_ws.connect())
        await self._control_ws.wait_connected()
        try:
            await self._control_ws.send({"event": "agent.hello", "sdk": get_sdk_info()})
        except Exception:
            pass

    async def call(
        self,
        to: str,
        *,
        timeout: int = 60,
        machine_detection: Literal["Enable", "Hangup"] | None = None,
    ) -> CallSession:
        """발신 전화를 건다. CallSession을 즉시 리턴 (queued 상태).

        connect()가 호출되지 않은 상태면 자동으로 connect()를 먼저 수행한다.

        Args:
            to: 착신 번호.
            timeout: 발신 ring timeout (초).
            machine_detection: 자동응답기/음성사서함 감지(AMD). ``"Enable"`` 이면 감지 후
                ``AnsweredBy`` 통보(통화 계속), ``"Hangup"`` 이면 음성사서함 감지 시 자동 종료.
                ``None`` (기본)이면 인스턴스 default(생성자의 ``machine_detection``)를 따른다.
                우선순위: 호출 인자 > 인스턴스 default > 비활성.
        """
        await self.connect()

        import aiohttp as _aiohttp

        url = f"{self._base_url}/v1/accounts/{self._account_id}/calls"
        body: dict[str, object] = {"To": to, "From": self._from_number, "Timeout": timeout}
        effective_md = machine_detection if machine_detection is not None else self._machine_detection
        if effective_md in ("Enable", "Hangup"):
            body["MachineDetection"] = effective_md
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        async with _aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=headers) as resp:
                if resp.status != 201:
                    error = await resp.json()
                    err_msg = error.get("error", "")
                    err_code = error.get("code", "")
                    agent_err = AgentError(f"발신 실패 ({resp.status}): {err_msg}")
                    agent_err.status = resp.status
                    agent_err.code = err_code
                    raise agent_err
                data = await resp.json()

        call_session = CallSession(
            call_id=data["callId"],
            from_number=self._from_number,
            to_number=to,
            account_id=self._account_id,
            direction="outbound",
        )

        for event, handlers in self._event_handlers.items():
            for handler in handlers:
                call_session.on(event, handler)

        self._active_sessions[call_session.call_id] = call_session
        log.info(f"Outbound call initiated: {self._from_number} -> {to} ({call_session.call_id})")

        # originate 직후 prewarm 을 시작한다 — ring 구간(answer 이전)에 LLM 연결 +
        # greeting 생성을 흡수해 answer→first-audio latency 를 줄인다. call.ringing
        # 이벤트는 트렁크가 SIP 18x 를 안 올리면 도착하지 않을 수 있어 신뢰하지 않는다.
        # ringing/outbound_ready 핸들러의 prewarm 시작은 이 시점을 놓쳤을 때의 fallback.
        self._start_prewarm(call_session.call_id)

        return call_session

    async def _handle_incoming(self, data: dict[str, Any]) -> None:
        call_id = data["callId"]
        from_number = data.get("from", "")
        media_url = data.get("mediaUrl", "")

        log.info(f"Incoming call: {from_number} -> {self._from_number} ({call_id})")

        call = CallSession(
            call_id=call_id,
            from_number=from_number,
            to_number=self._from_number,
            account_id=self._account_id,
            direction="inbound",
        )

        for event, handlers in self._event_handlers.items():
            for handler in handlers:
                call.on(event, handler)

        self._active_sessions[call_id] = call

        if self._control_ws:
            await self._control_ws.send({"event": "call.accept", "callId": call_id})

        asyncio.create_task(self._safe_start_call_session(call, media_url))

    async def _safe_start_call_session(self, call: CallSession, media_url: str) -> None:
        """_start_call_session 의 예외를 잡아 control WS 로 call.session_failed 전송한다.

        OpenAI/Gemini API 키 누락 등 session.start() 단계 실패는 media WS connect
        에 도달하지 못해 call-engine 이 30 초간 무음 통화를 유지하게 만든다. 서버에
        즉시 알려서 fail-fast 시키고 _active_sessions 에서 정리한다.
        """
        try:
            await self._start_call_session(call, media_url)
        except Exception as e:
            log.error(
                "Session start failed for %s: %s",
                call.call_id,
                e,
                exc_info=True,
            )
            if self._control_ws:
                try:
                    await self._control_ws.send(
                        {
                            "event": "call.session_failed",
                            "callId": call.call_id,
                            "reason": type(e).__name__,
                            "message": str(e),
                        }
                    )
                except Exception:
                    pass
            self._active_sessions.pop(call.call_id, None)
            self._call_sessions.pop(call.call_id, None)

    def _inject_session_deps(
        self,
        session: Any,
        tools: ToolRegistry,
        *,
        recorder: AudioRecorder | None = None,
    ) -> None:
        """세션에 콜별 의존성(도구·내장도구·hold audio·recorder)을 duck-typing 으로 주입한다.

        prewarm 과 _start_call_session 양쪽에서 부른다. prewarm 은 세션이 LLM 에
        보낼 tool 스키마를 그 시점에 확정하므로(OpenAI session.update / Gemini live
        connect config), prewarm **전에** 최소 한 번은 도구가 들어가 있어야 한다.
        """
        if hasattr(session, "set_tool_registry"):
            session.set_tool_registry(tools)
        if recorder is not None and hasattr(session, "set_recorder"):
            session.set_recorder(recorder)
        if hasattr(session, "set_builtin_tools"):
            session.set_builtin_tools(self._builtin_tools)
        if self._hold_audio_chunks and hasattr(session, "set_hold_audio"):
            session.set_hold_audio(self._hold_audio_chunks)

    def _start_prewarm(self, call_id: str) -> None:
        """prewarm task 를 시작한다. 같은 callId 로 여러 번 불러도 안전하다.

        MCP 도구는 통화 시작 시점(_start_call_session)에야 등록되는데, 연결 후 도구
        변경이 불가능한 세션(Gemini Live)은 prewarm 하면 MCP 도구를 영영 못 쓴다.
        그런 조합에서는 prewarm 을 건너뛰고 기존 start() 경로로 간다.
        """
        if not self._prewarm_enabled or call_id in self._prewarm_tasks:
            return
        if self._mcp_servers and getattr(self._session, "tools_frozen_after_prewarm", False):
            log.info(
                "Skipping prewarm for %s — 세션이 연결 후 도구 변경을 지원하지 않아 "
                "MCP 도구가 누락된다.",
                call_id,
            )
            return
        self._prewarm_tasks[call_id] = asyncio.create_task(self._prewarm_session(call_id))

    async def _start_call_session(self, call: CallSession, media_url: str) -> None:
        with call_span(
            call.call_id,
            from_number=call.from_number,
            to_number=call.to_number,
        ):
            recorder: AudioRecorder | None = None
            if self._recording:
                recorder = AudioRecorder(self._recording_path, call.call_id)
                recorder.start()

            # 콜별 독립 ToolRegistry (동시 통화 간 MCP tool 충돌 방지)
            call_tools = self._tool_registry.fork()

            mcp_clients: list[MCPClient] = []
            if self._mcp_servers:
                log.debug("Starting %d MCP server(s) for call %s", len(self._mcp_servers), call.call_id)
                for server_config in self._mcp_servers:
                    from .mcp._stdio import MCPServerStdio as _Stdio

                    if isinstance(server_config, _Stdio):
                        span_ctx = mcp_connect_span("stdio", command=server_config.command)
                    else:
                        span_ctx = mcp_connect_span("http", url=server_config.url)
                    with span_ctx:
                        client = MCPClient(server_config)
                        await client.connect()
                        mcp_clients.append(client)
                call_tools.register_mcp_tools(mcp_clients)

            session = self._session
            self._inject_session_deps(session, call_tools, recorder=recorder)

            latest_media_ts = 0

            async def on_audio(ulaw: bytes, ts: int) -> None:
                nonlocal latest_media_ts
                latest_media_ts = ts
                ulaw = apply_ulaw_gain(ulaw, self._rx_gain)
                if recorder:
                    recorder.write_inbound(ulaw_to_pcm16(ulaw), media_ts_ms=ts)
                await session.feed_audio(ulaw, ts)

            async def on_dtmf(digit: str) -> None:
                self._on_dtmf_event(call, digit)

            media_ws = MediaWebSocket(
                url=media_url,
                api_key=self._api_key,
                on_audio=on_audio,
                on_start=lambda info: self._on_media_start(call, info),
                on_stop=lambda: self._on_media_stop(call, session),
                on_dtmf=on_dtmf,
            )

            async def send_audio(ulaw: bytes) -> None:
                ulaw = apply_ulaw_gain(ulaw, self._tx_gain)
                if recorder:
                    recorder.write_outbound(ulaw_to_pcm16(ulaw), media_ts_ms=latest_media_ts)
                await media_ws.send_audio(ulaw)

            call.bind_transport(
                send_audio=send_audio,
                send_clear=media_ws.send_clear,
                hangup=media_ws.graceful_close,
                send_dtmf=media_ws.send_dtmf,
                media_ws=media_ws,
                transfer=lambda params: self._control_ws.request_transfer(call.call_id, params),
            )

            self._call_sessions[call.call_id] = session

            await call._emit("call_start")

            # prewarm task 가 있으면 await 후 attach(), 없거나 실패면 기존 start() 경로.
            prewarm_task = self._prewarm_tasks.pop(call.call_id, None)
            prewarm_failed = call.call_id in self._prewarm_failed
            self._prewarm_failed.discard(call.call_id)
            if prewarm_task is not None and not prewarm_failed:
                try:
                    await prewarm_task
                    log.info(
                        f"[PREWARM-T] attach call_id={call.call_id} t={time.monotonic():.3f}"
                    )
                    await session.attach(call)
                    # attach 완료 — 이후 stop() 은 이 경로(finally)가 책임진다.
                    # _handle_ended/_handle_failed 의 cleanup 이 중복 stop() 하지 않도록 표시.
                    self._prewarm_attached.add(call.call_id)
                except Exception as e:
                    log.warning(f"prewarm await/attach failed, falling back to start: {e}")
                    # cancel + 명시적 수거 (B4: leak/warning 방지)
                    if not prewarm_task.done():
                        prewarm_task.cancel()
                    await asyncio.gather(prewarm_task, return_exceptions=True)
                    await session.start(call)
            else:
                if prewarm_task is not None and not prewarm_task.done():
                    prewarm_task.cancel()
                if prewarm_task is not None:
                    await asyncio.gather(prewarm_task, return_exceptions=True)
                await session.start(call)

            # Send call telemetry (best-effort)
            telemetry = session.get_telemetry() if hasattr(session, "get_telemetry") else None
            if isinstance(telemetry, dict):
                session_tools = call_tools.to_openai_tools() if call_tools else []
                telemetry["toolCount"] = len(session_tools) if session_tools else 0
                telemetry["mcpServerCount"] = len(self._mcp_servers) if self._mcp_servers else 0
                telemetry["builtinTools"] = [t.value for t in self._builtin_tools] if self._builtin_tools else []
                telemetry["recordingEnabled"] = self._recording
                try:
                    await self._control_ws.send(
                        {"event": "call.telemetry", "callId": call.call_id, "telemetry": telemetry}
                    )
                except Exception:
                    pass

            try:
                await media_ws.connect()
            except Exception:
                call.metrics.record_end_reason("error")
            finally:
                # Determine end reason
                if not call.metrics.end_reason:
                    if call.status == "ended":
                        call.metrics.record_end_reason("user_hangup")
                    else:
                        call.metrics.record_end_reason("agent_hangup")

                # Send call metrics (best-effort)
                try:
                    await self._control_ws.send(
                        {"event": "call.metrics", "callId": call.call_id, "metrics": call.metrics.to_dict()}
                    )
                except Exception:
                    pass

                await session.stop()
                if mcp_clients:
                    call_tools.clear_mcp_tools()
                    for c in mcp_clients:
                        await c.close()
                if recorder:
                    recorder.stop()
                await call._emit("call_end")
                call._mark_ended()
                self._active_sessions.pop(call.call_id, None)
                self._call_sessions.pop(call.call_id, None)
                self._prewarm_attached.discard(call.call_id)

    async def _on_media_start(self, call: CallSession, info: dict[str, Any]) -> None:
        log.info(f"Media stream started: {call.call_id}")

    async def _on_media_stop(self, call: CallSession, session: Session) -> None:
        log.info(f"Media stream stopped: {call.call_id}")
        await session.stop()

    async def _handle_outbound_ready(self, data: dict[str, Any]) -> None:
        call_id = data["callId"]
        media_url = data.get("mediaUrl", "")
        call = self._active_sessions.get(call_id)
        if not call:
            log.warning(f"Unknown outbound call: {call_id}")
            if self._control_ws:
                try:
                    await self._control_ws.send(
                        {
                            "event": "call.session_failed",
                            "callId": call_id,
                            "reason": "UnknownOutboundCall",
                            "message": (
                                "SDK 의 _active_sessions 에 callId 가 없습니다. "
                                "agent.call() 메서드 대신 REST API 를 직접 호출했거나 "
                                "발신 후 SDK 프로세스가 재시작된 경우입니다."
                            ),
                        }
                    )
                except Exception:
                    pass
            return
        call.status = "in-progress"
        log.info(f"Outbound call answered: {call.from_number} -> {call.to_number} ({call_id})")

        # prewarm 은 보통 _handle_ringing(ring 구간)에서 이미 시작됐다. 여기서는
        # ringing 이벤트가 오지 않은 경우의 fallback 으로만 시작한다.
        # _start_call_session 이 prewarm task 를 await 후 attach() 로 부착한다.
        # prewarm_enabled=False 면 기존 start() 경로로 동작.
        self._start_prewarm(call_id)

        asyncio.create_task(self._safe_start_call_session(call, media_url))

    async def _prewarm_session(self, call_id: str) -> None:
        """outbound_ready 시점에 LLM WS 를 미리 연결한다.

        실패/timeout 시 _prewarm_failed 에 등록하여 _start_call_session 이
        기존 start() 경로로 fallback 한다.
        """
        t0 = time.monotonic()
        log.info(f"[PREWARM-T] start call_id={call_id} t={t0:.3f}")
        try:
            # prewarm 은 tool 스키마를 LLM 에 확정 전송한다. 여기서 주입하지 않으면
            # @agent.tool 로 등록한 도구가 통째로 빠진 채 세션이 시작된다 —
            # _start_call_session 의 주입은 answer 이후라 이미 늦다.
            self._inject_session_deps(self._session, self._tool_registry.fork())
            await asyncio.wait_for(self._session.prewarm(), timeout=10.0)
            log.info(
                f"[PREWARM-T] done call_id={call_id} elapsed_ms={(time.monotonic() - t0) * 1000:.0f}"
            )
        except Exception as e:
            log.warning(f"[PREWARM-T] failed call_id={call_id} err={e}")
            self._prewarm_failed.add(call_id)

    async def _handle_ringing(self, data: dict[str, Any]) -> None:
        call_id = data.get("callId", "")
        call = self._active_sessions.get(call_id)
        if call:
            call.status = "ringing"
            log.info(f"Outbound call ringing: {call_id}")

            # ring 구간(answer 이전)에 prewarm 을 미리 시작한다 — LLM WS 연결 +
            # greeting 생성을 ring 시간으로 흡수해 answer→first-audio latency 를 줄인다.
            # outbound_ready 에서의 prewarm 시작은 ringing 이 안 온 경우의 fallback.
            if self._prewarm_enabled and call_id not in self._prewarm_tasks:
                self._prewarm_tasks[call_id] = asyncio.create_task(
                    self._prewarm_session(call_id)
                )

    async def _cleanup_prewarm(self, call_id: str) -> None:
        """attach 전에 hangup/실패가 발생한 prewarm 세션을 정리한다 (LLM WS leak 방지).

        prewarm task 를 수거한 뒤, 아직 attach 되지 않았으면 session.stop() 으로
        열린 연결과 receive 루프를 닫는다. 이미 attach 된 통화면 정상 종료 경로
        (_start_call_session finally)가 stop() 을 책임지므로 중복 호출하지 않는다.
        """
        prewarm_task = self._prewarm_tasks.pop(call_id, None)
        attached = call_id in self._prewarm_attached
        self._prewarm_failed.discard(call_id)
        self._prewarm_attached.discard(call_id)
        if prewarm_task is None or attached:
            return
        if not prewarm_task.done():
            prewarm_task.cancel()
        await asyncio.gather(prewarm_task, return_exceptions=True)
        # prewarm 이 이미 연결을 열었으면 task cancel 만으로는 닫히지 않는다 — stop() 필요.
        try:
            await self._session.stop()
        except Exception as e:
            log.warning(f"prewarm cleanup stop() failed for {call_id}: {e}")

    async def _handle_failed(self, data: dict[str, Any]) -> None:
        call_id = data.get("callId", "")
        reason = data.get("reason", "failed")
        call = self._active_sessions.get(call_id)
        if call:
            call.status = reason
            call.ended_status = reason
            log.info(f"Outbound call failed: {call_id} ({reason})")
            await call._emit("call_failed", reason)
            call._mark_ended(reason)
            self._active_sessions.pop(call_id, None)
        await self._cleanup_prewarm(call_id)

    async def _handle_ended(self, data: dict[str, Any]) -> None:
        call_id = data.get("callId", "")
        # 서버는 종료 사유를 status 로 싣는다(completed/no-answer/busy/rejected/canceled/
        # failed). 예전에는 이 값을 버리고 전부 'completed' 로 표시해 상대가 받지 않은
        # 통화를 성사된 통화와 구분할 수 없었다.
        status = data.get("status") or "completed"
        call = self._active_sessions.get(call_id)
        log.info(f"Call ended (server): {call_id} (status={status})")
        if call:
            call.status = status
            call.ended_status = status
            if status != "completed":
                # 미연결 종료. call_start 가 없었으므로 call_end 도 발화되지 않는다 —
                # 이 이벤트가 발신 실패를 알 수 있는 유일한 통로다.
                log.info(f"Outbound call not connected: {call_id} ({status})")
                await call._emit("call_failed", status)
            call._mark_ended(status)
        self._active_sessions.pop(call_id, None)
        self._call_sessions.pop(call_id, None)
        await self._cleanup_prewarm(call_id)

    def _on_dtmf_event(self, call: CallSession, digit: str) -> None:
        """DTMF 수신 시 호출. collector 또는 패시브 모드로 라우팅."""
        asyncio.create_task(call._emit("dtmf", digit))

        # Always route to queue — collector may not be active yet (tool call timing)
        call._route_dtmf(digit)

        if call._dtmf_collector_active:
            asyncio.create_task(call.clear_audio())
            return

        call._passive_dtmf_buffer.append(digit)
        call._passive_dtmf_task = asyncio.create_task(self._flush_passive_dtmf(call))

    async def _flush_passive_dtmf(self, call: CallSession) -> None:
        """이 통화의 패시브 DTMF 버퍼를 debounce 후 flush 해 LLM 에 주입한다.

        앞선 flush 를 cancel 하지 않고, 깨어난 쪽이 "내가 아직 최신인가"를 확인해 스스로
        물러난다. cancel 방식은 이미 sleep 을 지나 feed_dtmf 안에 들어간 flush 까지 잘라서
        주입을 중간에 끊을 수 있었다. _mark_ended 가 _passive_dtmf_task 를 None 으로
        비우므로 통화가 끝난 뒤 깨어난 flush 도 같은 검사에 걸려 아무것도 하지 않는다.
        """
        await asyncio.sleep(self._passive_dtmf_debounce_ms / 1000)
        if call._passive_dtmf_task is not asyncio.current_task():
            return
        digits = "".join(call._passive_dtmf_buffer)
        call._passive_dtmf_buffer.clear()
        call._passive_dtmf_task = None
        if not digits:
            return
        session_obj = self._call_sessions.get(call.call_id)
        if session_obj:
            await session_obj.feed_dtmf(digits)
