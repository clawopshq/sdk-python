"""prewarm 단계에서 사용되는 CallSession stub.

실제 통화가 아직 attach 되지 않은 상태에서 Session 구현체가 호출하는
send_audio / _emit / metrics 를 흡수한다. attach() 시점에 drain_buffer() 로
누적된 audio chunk 들을 실제 CallSession 에 flush 한다.
"""
from __future__ import annotations

import logging
import time as _time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .._session import CallSession

log = logging.getLogger("clawops.agent.prewarm")


class _MetricsStub:
    def record_tool_call(self) -> None:
        pass

    def record_interrupt(self) -> None:
        pass


class _BufferingCall:
    """prewarm 단계의 CallSession 역할.

    Session 구현체가 `self._call.send_audio(...)` 를 호출하면 메모리 버퍼에 쌓는다.
    attach() 시 drain_buffer() 로 꺼내 실제 CallSession 으로 flush.

    _emit 으로 들어오는 transcript / tool_call 등 이벤트는 prewarm 동안 흘려보낸다.
    하지만 silent drop 은 디버깅 어려우므로 event name 별 카운터로 누적하여
    drain 시 로깅한다.
    """

    def __init__(self) -> None:
        self._buffer: list[bytes] = []
        self.metrics = _MetricsStub()
        self._dropped_events: dict[str, int] = {}

    async def send_audio(self, chunk: bytes) -> None:
        self._buffer.append(chunk)

    async def clear_audio(self) -> None:
        # prewarm 중 barge-in/truncation 시 버퍼링된 인사말 오디오를 버린다.
        # 모든 세션(OpenAI/Gemini/Pipeline/LiveKit)이 attach 전에 self._call.clear_audio()
        # 를 호출할 수 있으므로 CallSession.clear_audio 계약을 그대로 미러한다.
        self._buffer.clear()

    async def _emit(self, *args: Any, **kwargs: Any) -> None:
        # 첫 인자가 event name 이라고 가정 (CallSession._emit 시그니처와 일치).
        event_name = "?"
        if args:
            first = args[0]
            if isinstance(first, str):
                event_name = first
        self._dropped_events[event_name] = self._dropped_events.get(event_name, 0) + 1

    def drain_buffer(self) -> list[bytes]:
        out = self._buffer
        self._buffer = []
        return out

    def drain_dropped_events(self) -> dict[str, int]:
        out = self._dropped_events
        self._dropped_events = {}
        return out


async def drain_into(prev: Any, call: "CallSession") -> bool:
    """`prev` 가 `_BufferingCall` 이면 누적 버퍼/이벤트를 `call` 로 flush.

    OpenAI / Gemini / PipelineSession 의 `attach()` 가 공통으로 호출하는 헬퍼.
    `prev` 가 BufferingCall 이 아니면 (예: 두 번째 attach, 또는 prewarm 생략 경로)
    no-op.

    반환값: 버퍼에서 audio chunk 를 한 개 이상 flush 했으면 True. 호출 측 Session
    은 이 결과로 `_prewarm_first_audio_logged` 를 갱신하여, prewarm 미응답
    (drained empty) 케이스에서 첫 실시간 audio.delta 송출 시 PREWARM-T first-audio
    마커를 한 번 더 찍어야 할지 결정한다.
    """
    if not isinstance(prev, _BufferingCall):
        return False

    drained = prev.drain_buffer()
    flushed = bool(drained)
    if drained:
        log.info(
            f"[PREWARM-T] first-audio call_id={getattr(call, 'call_id', '?')} "
            f"t={_time.monotonic():.3f} buffered_chunks={len(drained)} source=prebuffer"
        )
    for chunk in drained:
        await call.send_audio(chunk)

    dropped = prev.drain_dropped_events()
    if dropped:
        log.info(
            f"[PREWARM] dropped events during prewarm "
            f"call_id={getattr(call, 'call_id', '?')} events={dropped}"
        )
    return flushed


def log_first_realtime_audio(call: Any) -> None:
    """prewarm prebuffer 가 비었던 케이스에서 첫 실시간 audio chunk 송출 시 호출.

    호출자(Session)는 attach 시 받은 drain_into 반환값과 자체 플래그로 한 번만 호출하도록
    가드한다."""
    log.info(
        f"[PREWARM-T] first-audio call_id={getattr(call, 'call_id', '?')} "
        f"t={_time.monotonic():.3f} buffered_chunks=0 source=realtime"
    )
