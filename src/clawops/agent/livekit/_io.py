"""LiveKit AudioInput/AudioOutput <-> ClawOps Media WS 브리지.

LiveKit 의 AgentSession 은 room 없이도 동작한다 — `session.input.audio` /
`session.output.audio` 에 커스텀 구현을 꽂으면 된다. 이 모듈이 그 구현이다.

전화망 쪽 wire format 은 G.711 μ-law 8kHz, 160바이트(20ms) 프레임이다.
AudioOutput 이 `sample_rate=8000` 을 선언하므로 프레임워크가 TTS 출력을 8kHz 로
알아서 리샘플해준다. 우리는 μ-law 인코딩만 한다.

참조 구현: livekit `cli/tcp_console.py:TcpAudioOutput` — 같은 문제(비-WebRTC
transport 를 AgentSession 에 물리기)를 푸는 LiveKit 자신의 코드다.

⚠️ AudioOutput 계약 3가지 — 어기면 조용히 깨진다:

1. 이 싱크는 반드시 `TranscriptSynchronizer` 로 감싸야 한다. 감싸지 않으면
   barge-in 시 `synchronized_transcript` 가 None 이라 LLM 컨텍스트에 **말하지 않은
   전체 텍스트**가 기록된다. `_session.py` 가 이 래핑을 책임진다.
2. 첫 프레임에서 `on_playback_started()` 를 반드시 호출한다. 이걸 빼면
   `first_frame_fut` 이 resolve 되지 않아 assistant 메시지가 히스토리에서
   **통째로 사라진다**.
3. `on_playback_finished()` 는 세그먼트당 정확히 한 번, `flush()` 가 띄운
   태스크에서만 호출한다. `clear_buffer()` 에서 호출하면 재생 회계가 깨진다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from livekit import rtc
from livekit.agents.voice import io

from .._audio import pcm16_to_ulaw, ulaw_to_pcm16

log = logging.getLogger("clawops.agent.livekit")

SAMPLE_RATE = 8000
"""전화망 wire rate. AudioOutput 이 이걸 선언하면 프레임워크가 여기 맞춰 리샘플한다."""

FRAME_BYTES = 160
"""μ-law 20ms = 160바이트."""

ULAW_SILENCE = b"\xff"
"""μ-law 무음 — 마지막 프레임 패딩용 (`_pipeline_session.py` 와 동일)."""

_MARK_TIMEOUT_MARGIN = 10.0
"""mark 대기 timeout = 밀어넣은 오디오 길이 + 이 여유."""

_SENTINEL = object()


class ClawOpsAudioInput(io.AudioInput):
    """통화 인바운드 오디오(μ-law) -> rtc.AudioFrame 스트림.

    `LiveKitSession.feed_audio()` 가 `push_ulaw()` 로 밀어넣는다.
    """

    def __init__(self) -> None:
        super().__init__(label="ClawOps")
        self._queue: asyncio.Queue[Any] = asyncio.Queue()

    def push_ulaw(self, ulaw: bytes) -> None:
        """G.711 μ-law 청크를 PCM16 프레임으로 바꿔 큐에 넣는다."""
        pcm = ulaw_to_pcm16(ulaw)
        if not pcm:
            return
        frame = rtc.AudioFrame(
            data=pcm,
            sample_rate=SAMPLE_RATE,
            num_channels=1,
            samples_per_channel=len(pcm) // 2,
        )
        self._queue.put_nowait(frame)

    def end_input(self) -> None:
        """스트림 종료 — `__anext__` 가 StopAsyncIteration 을 내게 한다."""
        self._queue.put_nowait(_SENTINEL)

    async def __anext__(self) -> rtc.AudioFrame:
        item = await self._queue.get()
        if item is _SENTINEL:
            raise StopAsyncIteration
        return item  # type: ignore[no-any-return]


class ClawOpsAudioOutput(io.AudioOutput):
    """AgentSession 출력 -> μ-law 160바이트 프레임 -> CallSession.send_audio().

    재생 완료(playout) 판정은 Media WS 의 `mark` 로 한다 — 플랫폼이 큐에 쌓인
    오디오를 다 내보낸 뒤 mark 를 에코해준다. prewarm 중(`_BufferingCall`)에는
    media_ws 가 없으므로 즉시 완료로 본다(실제로 아직 아무것도 재생되지 않지만,
    버퍼는 attach 시 전부 flush 되므로 "전부 재생됨"이 맞다).
    """

    def __init__(self, call: Any) -> None:
        super().__init__(
            label="ClawOps",
            next_in_chain=None,
            sample_rate=SAMPLE_RATE,
            capabilities=io.AudioOutputCapabilities(pause=False),
        )
        self._call = call
        self._pushed_duration: float = 0.0
        self._capture_start: float = 0.0
        self._tail: bytes = b""
        self._flush_task: asyncio.Task[None] | None = None
        self._interrupted_ev = asyncio.Event()
        self._attached_ev = asyncio.Event()
        self._bg_tasks: set[asyncio.Task[None]] = set()

    def set_call(self, call: Any) -> None:
        """attach() 시 실제 CallSession 으로 교체 (prewarm -> 실제 통화).

        prewarm 중 버퍼링된 오디오는 지금부터 실제로 재생되기 시작하므로
        경과 시간 기준점을 여기로 옮긴다 — 안 그러면 링 구간까지 "재생됐다"고
        계산돼서 barge-in 시 절단 위치가 뒤로 밀린다.
        """
        self._call = call
        if self._pushed_duration:
            self._capture_start = time.monotonic()
        self._attached_ev.set()

    # ── AudioOutput 구현 ────────────────────────────────────────

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        await super().capture_frame(frame)

        # 이전 세그먼트의 flush 가 아직 진행 중이면 기다린다 (TcpAudioOutput 과 동일).
        if self._flush_task and not self._flush_task.done():
            log.warning("capture_frame called while previous flush is in progress")
            await self._flush_task

        if not self._pushed_duration:
            self._capture_start = time.monotonic()
            # ⚠️ 계약 2: 이걸 빼면 assistant 메시지가 히스토리에서 사라진다.
            self.on_playback_started(created_at=time.time())

        self._pushed_duration += frame.duration

        # 이 프레임의 모든 청크는 한 통화로 보낸다 (attach 스왑 중 프레임 내 분할 방지).
        call = self._call
        ulaw = self._tail + pcm16_to_ulaw(bytes(frame.data))
        off = 0
        while off + FRAME_BYTES <= len(ulaw):
            await call.send_audio(ulaw[off : off + FRAME_BYTES])
            off += FRAME_BYTES
        self._tail = ulaw[off:]

    def flush(self) -> None:
        super().flush()
        # 캡처된 세그먼트가 없으면 닫을 것도 없다.
        if self._pending_playback_count <= 0:
            self._tail = b""
            return
        # ⚠️ 이전 flush 태스크를 취소하지 않는다 — 취소하면 그 세그먼트의
        #    on_playback_finished 가 유실돼 프레임워크 wait_for_playout 이 영구 대기한다.
        #    대신 _flush_and_wait 이 이전 태스크를 먼저 await 해 직렬화한다.
        prev = self._flush_task
        self._flush_task = asyncio.create_task(self._flush_and_wait(prev))

    def clear_buffer(self) -> None:
        # ⚠️ 계약 3: 여기서 on_playback_finished 를 부르지 않는다.
        #    _flush_and_wait 이 _interrupted_ev 를 보고 한 번만 쏜다.
        self._tail = b""
        if self._pushed_duration:
            self._interrupted_ev.set()
        # prewarm 중 _BufferingCall.clear_audio 는 버퍼를 비운다(실제 통화면 큐 flush).
        self._spawn(self._call.clear_audio())

    def close(self) -> None:
        """세션 종료 시 남은 재생 태스크를 정리한다 (닫힌 통화 참조 방지)."""
        self._interrupted_ev.set()  # prewarm 대기 중인 _flush_and_wait 을 깨운다
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        for task in list(self._bg_tasks):
            task.cancel()

    # ── 내부 ────────────────────────────────────────────────────

    def _spawn(self, coro: Any) -> None:
        """sync 컨텍스트에서 async 호출 — GC 방지용으로 참조를 붙든다."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._on_bg_done)

    def _on_bg_done(self, task: asyncio.Task[Any]) -> None:
        self._bg_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.warning(f"background task failed: {task.exception()}")

    def _media_ws(self) -> Any | None:
        """연결된 Media WS. prewarm 중(_BufferingCall)에는 None."""
        ws = getattr(self._call, "_media_ws", None)
        if ws is None or not ws.is_connected:
            return None
        return ws

    async def _race_interrupt(self, awaitable: Any) -> bool:
        """`awaitable` 과 barge-in 중 먼저 오는 걸 기다린다. 반환: awaitable 이 이겼는가.

        진 쪽은 취소한다 — wait_for_mark 은 취소돼도 waiter 를 회수한다(_media_ws.py).
        """
        main = asyncio.ensure_future(awaitable)
        interrupt = asyncio.create_task(self._interrupted_ev.wait())
        try:
            await asyncio.wait([main, interrupt], return_when=asyncio.FIRST_COMPLETED)
            won = bool(main.done())
            if won:
                # main 은 우리만 소유하며 아직 취소 전이므로 .exception() 이 안전하다.
                # 예외가 있으면 consume 해 "never retrieved" 경고를 막는다.
                main.exception()
            return won
        finally:
            main.cancel()
            interrupt.cancel()

    async def _flush_and_wait(self, prev: asyncio.Task[None] | None) -> None:
        # 이전 세그먼트의 flush 태스크를 먼저 끝낸다 (취소하지 않고 직렬화).
        if prev is not None and not prev.done():
            try:
                await prev
            except asyncio.CancelledError:
                # 우리 자신이 취소된 경우엔 전파해야 close() 가 실제로 멈춘다.
                # prev 가 취소된 경우엔 무시하고 우리 세그먼트를 처리한다.
                if asyncio.current_task().cancelling():  # type: ignore[union-attr]
                    raise
            except Exception as e:
                log.warning(f"previous flush segment failed: {e}")

        # prev 가 자기 세그먼트를 닫은 뒤 남은 미완료 세그먼트가 없으면 종료.
        if self._pending_playback_count <= 0:
            self._tail = b""
            return

        interrupted = True
        try:
            # 남은 자투리를 무음으로 패딩해 한 프레임으로 내보낸다.
            tail, self._tail = self._tail, b""
            if tail:
                await self._call.send_audio(tail + ULAW_SILENCE * (FRAME_BYTES - len(tail)))

            interrupted = await self._await_playout()
        except Exception:
            # WS 사망 등 전송 오류 — interrupted(=True) 로 처리하고 프레임워크로
            # 전파하지 않는다(전파하면 capture 루프가 깨진다). CancelledError 는
            # Exception 이 아니므로 여기 안 걸리고 그대로 전파되며 finally 가 emit 한다.
            log.warning("playout error, treating as interrupted", exc_info=True)
        finally:
            # ⚠️ 계약 3: 세그먼트당 정확히 한 번 — 취소되더라도 반드시 emit 한다.
            #    (안 그러면 segment count 가 어긋나 wait_for_playout 이 영구 대기한다.)
            if interrupted:
                played = min(max(0.0, time.monotonic() - self._capture_start), self._pushed_duration)
            else:
                played = self._pushed_duration
            self.on_playback_finished(playback_position=played, interrupted=interrupted)
            self._pushed_duration = 0.0
            self._interrupted_ev.clear()

    async def _await_playout(self) -> bool:
        """재생이 끝나거나 barge-in 될 때까지 대기. 반환값: interrupted 여부."""
        ws = self._media_ws()
        if ws is None:
            # prewarm 중 — 아직 재생 주체가 없다. 여기서 바로 완료로 처리하면
            # attach 후 인사말이 실제로 나갈 때 barge-in 해도 컨텍스트가 안 잘린다
            # (모델이 인사말을 다 했다고 믿는다). attach 될 때까지 기다렸다가
            # 그때부터 진짜 fence 를 건다.
            if not await self._race_interrupt(self._attached_ev.wait()):
                return True  # attach 전에 끊겼다
            ws = self._media_ws()
            if ws is None:
                # attach 됐는데도 WS 가 없다 (이미 종료된 통화 등) — fence 불가.
                return self._interrupted_ev.is_set()

        # send_mark 는 WS 로 즉시 나가지만 send_audio 는 로컬 큐에 쌓인다.
        # flush() 로 큐를 비우지 않으면 mark 가 오디오를 추월한다 (_graceful_hangup 과 동일).
        await ws.flush()

        mark_name = f"lk-{int(time.monotonic() * 1e6)}"
        await ws.send_mark(mark_name)

        # mark echo(재생 완료)와 barge-in 중 먼저 오는 것을 기다린다.
        return not await self._race_interrupt(
            ws.wait_for_mark(mark_name, timeout=self._pushed_duration + _MARK_TIMEOUT_MARGIN)
        )
