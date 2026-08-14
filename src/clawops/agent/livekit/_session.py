"""LiveKitSession — 유저의 LiveKit AgentSession 을 ClawOps `Session` Protocol 로 감싼다.

`ClawOpsAgent` 는 `Session`(`pipeline/_base.py`) 만 알고 통화를 굴린다. 이 클래스가
그 계약을 구현하면서 내부적으로 LiveKit AgentSession 을 돌린다 — room 없이.

유저는 관용적인 LiveKit 코드를 그대로 쓴다. `Agent` 서브클래스와 `AgentSession`
설정은 손대지 않아도 되고, 유일한 차이는 `session.start(room=...)` 을 우리가
대신 부른다는 것이다 (room 없이).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Awaitable, Callable, Tuple

from livekit.agents import Agent, AgentSession
from livekit.agents.llm import function_tool
from livekit.agents.llm.tool_context import get_fnc_tool_names
from livekit.agents.utils import http_context, is_given
from livekit.agents.voice.transcription import TranscriptSynchronizer

from .._session import CallSession
from .._tool import ToolRegistry
from ..pipeline._buffering_call import _BufferingCall, drain_into
from ._io import ClawOpsAudioInput, ClawOpsAudioOutput
from ._toolset import ClawOpsPhoneTools

log = logging.getLogger("clawops.agent.livekit")

CreateFn = Callable[["CallSession | None"], Awaitable[Tuple[AgentSession[None], Agent]]]
"""통화당 1회 호출되는 팩토리. (AgentSession, Agent) 를 반환한다.

`call` 은 착신에서는 실제 CallSession, 발신 prewarm 중에는 None 이다
(prewarm 은 응답 전에 도는데 `Session.prewarm()` 이 call 을 넘겨주지 않는다).
"""


class LiveKitSession:
    """LiveKit AgentSession 을 ClawOps 전화망에 물린다.

        async def create(call):
            session = AgentSession(
                llm=openai.realtime.RealtimeModel(modalities=["text"]),
                tts=cartesia.TTS(model="sonic-3.5", language="ko"),
            )
            return session, MyAgent()

        agent = ClawOpsAgent(
            from_="07012341234",
            session_factory=lambda: LiveKitSession(create),
        )

    동시통화를 받으려면 위처럼 `session_factory=` 로 넘긴다 — 통화마다 이 클래스가 새로
    만들어져 `_session`/`_agent`/`_target` 이 통화 안에 갇힌다. `session=` 으로 인스턴스를
    직접 넘기면 통화 간에 그 객체를 공유하므로 동시통화 1건까지다.
    """

    def __init__(self, create: CreateFn) -> None:
        self._create = create

        self._session: AgentSession[None] | None = None
        self._agent: Agent | None = None
        self._input: ClawOpsAudioInput | None = None
        self._output: ClawOpsAudioOutput | None = None
        self._toolset: ClawOpsPhoneTools | None = None
        self._target: Any = None  # CallSession | _BufferingCall
        self._user_tools: list[Any] = []  # 유저가 준 원본 도구 (우리 것 추가 전 스냅샷)

        self._builtin_tools: set[Any] | None = None
        self._tool_registry: ToolRegistry | None = None
        self._http_stack: contextlib.AsyncExitStack | None = None
        # transcript emit 은 LiveKit 의 동기 이벤트 콜백에서 fire-and-forget 로 돈다.
        # GC 로 태스크가 사라지지 않게 참조를 잡아둔다.
        self._emit_tasks: set[asyncio.Task[None]] = set()

    # ── ClawOpsAgent 가 duck-typing 으로 부르는 setter ──────────

    def set_builtin_tools(self, tools: set[Any]) -> None:
        self._builtin_tools = tools

    def set_tool_registry(self, registry: ToolRegistry) -> None:
        self._tool_registry = registry

    # ── 프로세스 수명 훅 (ClawOpsAgent.connect/disconnect 에서 root task 로 호출) ──

    async def session_setup(self) -> None:
        """HTTP 기반 LiveKit 플러그인(Cartesia/Deepgram 등)을 위한 준비.

        이들은 워커의 공유 aiohttp 세션(`http_context`)을 통해 연결한다. room-less
        로는 워커가 없으므로 우리가 열어줘야 한다. 이 훅은 control WS task 와 모든
        통화 task 의 공통 조상인 root task 에서 실행되므로, 여기서 연 컨텍스트를
        하위 TTS/STT task 들이 상속받는다. (이미 열려 있으면 open() 이 no-op.)
        """
        if self._http_stack is not None:
            return
        # 컨텍스트 진입에 성공한 뒤에야 self 에 저장한다 — 실패 시 _http_stack 이
        # 빈 채로 남으면 이후 session_setup 이 계속 early-return 해 http_context 가
        # 영영 안 열린다(무음 통화).
        stack = contextlib.AsyncExitStack()
        await stack.enter_async_context(http_context.open())
        self._http_stack = stack

    async def session_teardown(self) -> None:
        if self._http_stack is not None:
            await self._http_stack.aclose()
            self._http_stack = None

    # ── Session Protocol ────────────────────────────────────────

    async def start(self, call: CallSession) -> None:
        await self._boot(call)

    async def prewarm(self) -> None:
        await self._boot(None)

    async def attach(self, call: CallSession) -> None:
        # boot 성공 신호는 _session (부분 완료된 boot 은 _output 만 set 돼 있을 수 있다).
        if self._session is None or self._output is None or self._agent is None:
            # prewarm 이 실패했거나 건너뛴 경로 — 그냥 새로 띄운다.
            await self._boot(call)
            return

        prev = self._target
        self._output.set_call(call)
        self._target = call
        # ⚠️ 아웃바운드 prewarm 은 _boot(None) 이 setter 실행 전에 돌아 도구를
        # 비운 채 시작한다. setter 는 이제 실행됐으므로 여기서 도구를 다시 붙인다.
        await self._apply_tools(call)
        await drain_into(prev, call)

    async def feed_audio(self, audio: bytes, timestamp: int) -> None:
        if self._input is not None:
            self._input.push_ulaw(audio)

    async def feed_dtmf(self, digits: str) -> None:
        if self._session is None:
            return
        # generate_reply 는 SpeechHandle 을 반환하는 동기 메서드다. teardown 중에는
        # 세션 activity 가 이미 정리돼 RuntimeError 를 던지므로 삼킨다.
        try:
            self._session.generate_reply(user_input=f"[전화 키패드 입력] {digits}")
        except RuntimeError as e:
            log.debug(f"feed_dtmf skipped (session not running): {e}")

    async def stop(self) -> None:
        if self._input is not None:
            self._input.end_input()
        await self._close_agent_session()

    async def _close_agent_session(self) -> None:
        """현재 AgentSession + 출력 태스크를 정리한다.

        stop() 과 _boot() 재진입(attach 실패 폴백)이 공통으로 부른다 — 후자에서
        기존 세션을 닫지 않으면 LLM WS 가 새고 두 세션이 같은 통화에 오디오를 보낸다.
        """
        if self._output is not None:
            self._output.close()
            self._output = None
        if self._session is not None:
            try:
                await self._session.aclose()
            except Exception as e:
                log.warning(f"AgentSession.aclose() failed: {e}")
            self._session = None

    def get_telemetry(self) -> dict[str, Any] | None:
        # TODO: conversation_item_added(ChatMessage.metrics.e2e_latency / .interrupted) 와
        # function_tools_executed 로 CallMetrics 를 채운다.
        # metrics_collected 는 1.6.5 에서 deprecated 이므로 쓰지 말 것.
        return None

    # ── 내부 ────────────────────────────────────────────────────

    async def _boot(self, call: CallSession | None) -> None:
        # 재진입(attach 실패 폴백) 시 이전 세션이 새지 않도록 먼저 닫는다.
        await self._close_agent_session()

        target: Any = call if call is not None else _BufferingCall()

        session, agent = await self._create(call)
        _validate(session, agent)

        self._input = ClawOpsAudioInput()
        self._output = ClawOpsAudioOutput(target)

        # ⚠️ TranscriptSynchronizer 는 필수다. room 없이는 이걸 안 씌우면
        # barge-in 시 synchronized_transcript 가 None 이라 LLM 컨텍스트에
        # "말하지 않은 전체 텍스트"가 기록된다. _io.py 계약 1 참조.
        sync = TranscriptSynchronizer(
            next_in_chain_audio=self._output, next_in_chain_text=None
        )
        session.input.audio = self._input
        session.output.audio = sync.audio_output
        session.output.transcription = sync.text_output

        self._wire_transcripts(session)

        self._agent = agent
        # 우리 도구를 붙이기 전 유저 원본 도구를 스냅샷한다 (attach 재적용 시 누적 방지).
        self._user_tools = list(agent.tools)
        await self._apply_tools(call)

        # room= 을 넘기지 않는 것이 이 통합의 전부다.
        await session.start(agent)

        self._session = session
        self._target = target

    async def _apply_tools(self, call: CallSession | None) -> None:
        """유저 도구 + 브리지된 registry 도구 + 내장 전화 도구를 이름 충돌 없이 붙인다.

        아웃바운드 prewarm(_boot(None))은 setter 실행 전에 돌아 registry/builtin 이
        비어 있고, 그 사이엔 통화가 연결되지 않아 도구를 쓸 일도 없다. 그래서 이때는
        유저 도구만 올리고, attach() 가 setter 실행 후 실제 도구를 붙이는 유일한
        조립 지점이 된다.
        """
        if self._agent is None:
            return

        if call is None:  # prewarm — 아직 registry/builtin 이 없다
            await self._agent.update_tools(list(self._user_tools))
            return

        # 유저 도구가 Toolset 이면 .id 는 묶음 이름이라 멤버 도구 이름을 못 잡는다.
        # get_fnc_tool_names 로 Toolset 을 풀어 실제 함수 이름을 모은다 — 안 그러면
        # 내장 도구와 겹칠 때 flatten 이 "duplicate function name" 으로 통화를 떨군다.
        taken = set(get_fnc_tool_names(self._user_tools))
        bridged_all = _bridge_registry(self._tool_registry)
        bridged: list[Any] = []
        for tool, name in zip(bridged_all, get_fnc_tool_names(bridged_all)):
            if name in taken:
                continue  # 유저 도구가 우선 — 이름 충돌 시 건너뛴다
            taken.add(name)
            bridged.append(tool)

        # 내장 전화 도구 중 이름이 겹치는 것은 제외한다 (duplicate function name 방지).
        self._toolset = ClawOpsPhoneTools(enabled=self._builtin_tools, exclude_names=taken)
        self._toolset.set_call(call)

        await self._agent.update_tools([*self._user_tools, *bridged, self._toolset])

    def _wire_transcripts(self, session: AgentSession[None]) -> None:
        """LiveKit 의 최종 대화 항목을 ClawOps `transcript` 훅으로 흘려보낸다.

        네이티브 세션(OpenAI/Gemini/Pipeline)은 `call._emit("transcript", role, text)`
        를 부른다. `@agent.on("transcript")` 로 트랜스크립트를 모으는 기존 앱이 세션만
        바꿔도 그대로 돌게 하려면 여기서 같은 계약을 재현해야 한다.

        `conversation_item_added` 는 user·assistant 최종 메시지 양쪽에 대해 히스토리에
        커밋될 때 한 번씩 뜬다 — 부분(interim) transcript 중복 없이 최종만 얻는다.
        item 이 AgentHandoff 등 ChatMessage 가 아니면 role 이 없어 자연히 걸러진다.
        """

        def _on_item(ev: Any) -> None:
            item = getattr(ev, "item", None)
            role = getattr(item, "role", None)
            if role not in ("user", "assistant"):
                return
            text = getattr(item, "text_content", None)
            if not text:
                return
            self._forward_transcript(role, text)

        session.on("conversation_item_added", _on_item)

    def _forward_transcript(self, role: str, text: str) -> None:
        # 동기 콜백에서 async _emit 을 태워야 하므로 태스크로 띄운다. prewarm 중이면
        # _target 이 _BufferingCall 이라 _emit 이 드롭 카운트로 흡수한다 (무해).
        target = self._target
        if target is None:
            return
        task = asyncio.create_task(target._emit("transcript", role, text))
        self._emit_tasks.add(task)
        task.add_done_callback(self._emit_tasks.discard)


def _resolve(session: AgentSession[None], agent: Agent, attr: str) -> Any:
    """LiveKit 의 우선순위(Agent 가 Session 을 덮어씀)를 따라 컴포넌트를 찾는다."""
    value = getattr(agent, attr, None)
    if value is not None and is_given(value):
        return value
    return getattr(session, attr, None)


def _validate(session: AgentSession[None], agent: Agent) -> None:
    """LiveKit 이 조용히 넘어가는 두 조합을 우리가 막는다.

    LiveKit 은 `agent_activity.py` 에서 logger.error 만 찍고 진행하므로,
    실수하면 "말을 못 하는 에이전트"가 아무 예외 없이 배포된다.
    """
    llm = _resolve(session, agent, "llm")
    tts = _resolve(session, agent, "tts")

    caps = getattr(llm, "capabilities", None)
    audio_output = getattr(caps, "audio_output", None)
    if audio_output is None:
        return  # realtime 모델이 아니다 (일반 LLM + TTS 파이프라인)

    if not audio_output and tts is None:
        raise ValueError(
            "RealtimeModel 이 modalities=['text'] 인데 tts 가 없습니다 — "
            "에이전트가 소리를 내지 못합니다. AgentSession(tts=...) 를 주거나 "
            "modalities 에 'audio' 를 넣으세요."
        )
    if audio_output and tts is not None:
        log.warning(
            "RealtimeModel 이 오디오를 직접 출력하므로 tts 가 무시됩니다. "
            "TTS 를 쓰려면 RealtimeModel(modalities=['text']) 로 설정하세요."
        )


def _bridge_registry(registry: ToolRegistry | None) -> list[Any]:
    """`@agent.tool` / MCP 도구를 LiveKit function_tool 로 노출한다.

    LiveKit 경로에서는 보통 LiveKit 의 `@function_tool` 을 쓰지만, 기존 ClawOps
    데코레이터로 등록한 도구도 그대로 동작해야 한다.
    """
    if registry is None:
        return []

    tools: list[Any] = []
    for spec in registry.to_openai_tools():
        # ClawOps 는 realtime 포맷(name/parameters 가 최상위)으로 낸다.
        # MCP 도구 등 name 이 다른 위치에 있는 스키마는 방어적으로 건너뛴다.
        name = spec.get("name")
        if not name or "parameters" not in spec:
            continue
        raw_schema = {
            "name": name,
            "description": spec.get("description", ""),
            "parameters": spec["parameters"],
        }
        tools.append(
            function_tool(_make_raw_handler(registry, name), raw_schema=raw_schema)
        )
    return tools


def _make_raw_handler(registry: ToolRegistry, name: str) -> Callable[..., Awaitable[str]]:
    # raw function tool 의 인자 이름은 반드시 `raw_arguments` 여야 한다 (llm/utils.py:559).
    async def handler(raw_arguments: dict[str, object]) -> str:
        return await registry.call(name, dict(raw_arguments))

    handler.__name__ = name
    return handler
