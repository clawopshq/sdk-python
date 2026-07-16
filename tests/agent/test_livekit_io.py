"""ClawOps <-> LiveKit AudioInput/AudioOutput 브리지 계약 테스트.

여기서 고정하는 것은 _io.py 모듈 docstring 의 "계약 3가지"다. 셋 다 어겨도
예외가 나지 않고 조용히 오동작하므로, 통화 없이 오프라인으로 붙잡아 둔다.

핵심은 test_barge_in_truncates_history — 말을 끊었을 때 LLM 컨텍스트에 "실제로
들린 만큼"만 남는지. 이게 깨지면 모델이 하지도 않은 말을 했다고 믿는다.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

livekit_agents = pytest.importorskip("livekit.agents", reason="clawops[livekit] 미설치")

from livekit import rtc  # noqa: E402
from livekit.agents import Agent, AgentSession  # noqa: E402
from livekit.agents.voice import io  # noqa: E402
from livekit.agents.voice.transcription import TranscriptSynchronizer  # noqa: E402

from clawops.agent._audio import pcm16_to_ulaw  # noqa: E402
from clawops.agent._session import CallSession  # noqa: E402
from clawops.agent.livekit._io import (  # noqa: E402
    FRAME_BYTES,
    SAMPLE_RATE,
    ClawOpsAudioInput,
    ClawOpsAudioOutput,
)


class _FakeMediaWs:
    """Media WS 스텁. wait_for_mark 가 '플랫폼이 재생을 끝냄'을 흉내낸다."""

    def __init__(self, playout_delay: float = 0.0) -> None:
        self.is_connected = True
        self.marks: list[str] = []
        self.flushed = 0
        self.ops: list[str] = []  # flush/mark 순서 기록 (순서 계약 검증용)
        self._playout_delay = playout_delay

    async def flush(self) -> None:
        self.flushed += 1
        self.ops.append("flush")

    async def send_mark(self, name: str) -> None:
        self.marks.append(name)
        self.ops.append("mark")

    async def wait_for_mark(self, name: str, timeout: float = 5.0) -> None:
        await asyncio.sleep(min(self._playout_delay, timeout))


def _make_call(media_ws: _FakeMediaWs | None = None) -> CallSession:
    call = CallSession(
        call_id="test-call",
        from_number="01012345678",
        to_number="07012341234",
        account_id="AC123",
    )
    call._send_audio_fn = AsyncMock()
    call._send_clear_fn = AsyncMock()
    call._media_ws = media_ws
    return call


def _pcm_frame(samples: int) -> rtc.AudioFrame:
    return rtc.AudioFrame(
        data=b"\x00\x00" * samples,
        sample_rate=SAMPLE_RATE,
        num_channels=1,
        samples_per_channel=samples,
    )


def _sent_chunks(call: CallSession) -> list[bytes]:
    return [c.args[0] for c in call._send_audio_fn.await_args_list]


# ── AudioInput ──────────────────────────────────────────────────


async def test_audio_input_converts_ulaw_to_8k_frames() -> None:
    src = ClawOpsAudioInput()
    src.push_ulaw(b"\xff" * FRAME_BYTES)

    frame = await src.__anext__()

    assert frame.sample_rate == SAMPLE_RATE
    assert frame.num_channels == 1
    assert frame.samples_per_channel == FRAME_BYTES  # μ-law 1B == PCM16 1샘플
    assert frame.duration == pytest.approx(0.02)  # 20ms


async def test_audio_input_end_input_stops_iteration() -> None:
    src = ClawOpsAudioInput()
    src.end_input()

    with pytest.raises(StopAsyncIteration):
        await src.__anext__()


# ── AudioOutput: 프레이밍 ───────────────────────────────────────


async def test_output_emits_160_byte_ulaw_frames() -> None:
    call = _make_call()
    out = ClawOpsAudioOutput(call)

    await out.capture_frame(_pcm_frame(480))  # 60ms == 160B * 3

    chunks = _sent_chunks(call)
    assert len(chunks) == 3
    assert all(len(c) == FRAME_BYTES for c in chunks)
    assert chunks[0] == pcm16_to_ulaw(b"\x00\x00" * 160)


async def test_output_buffers_partial_frame_until_next_capture() -> None:
    call = _make_call()
    out = ClawOpsAudioOutput(call)

    await out.capture_frame(_pcm_frame(200))  # 160 + 40 자투리
    assert len(_sent_chunks(call)) == 1

    await out.capture_frame(_pcm_frame(120))  # 40 + 120 == 160
    assert len(_sent_chunks(call)) == 2
    assert all(len(c) == FRAME_BYTES for c in _sent_chunks(call))


async def test_output_pads_tail_with_silence_on_flush() -> None:
    call = _make_call(_FakeMediaWs())
    out = ClawOpsAudioOutput(call)

    await out.capture_frame(_pcm_frame(40))  # 160B 미만 — 자투리만
    assert _sent_chunks(call) == []

    out.flush()
    await asyncio.sleep(0.05)

    chunks = _sent_chunks(call)
    assert len(chunks) == 1
    assert len(chunks[0]) == FRAME_BYTES
    assert chunks[0][40:] == b"\xff" * (FRAME_BYTES - 40)  # μ-law 무음 패딩


# ── AudioOutput: 계약 2 (on_playback_started) ───────────────────


async def test_playback_started_emitted_once_on_first_frame() -> None:
    """계약 2. 이게 안 나가면 assistant 메시지가 히스토리에서 통째로 사라진다."""
    call = _make_call()
    out = ClawOpsAudioOutput(call)
    started: list[float] = []
    out.on("playback_started", lambda ev: started.append(ev.created_at))

    await out.capture_frame(_pcm_frame(160))
    await out.capture_frame(_pcm_frame(160))

    assert len(started) == 1


# ── AudioOutput: 계약 3 (playback_finished 정확히 1회) ──────────


async def test_playback_finished_once_per_segment() -> None:
    call = _make_call(_FakeMediaWs())
    out = ClawOpsAudioOutput(call)
    events: list[io.PlaybackFinishedEvent] = []
    out.on("playback_finished", events.append)

    await out.capture_frame(_pcm_frame(800))  # 100ms
    out.flush()
    await asyncio.sleep(0.05)

    assert len(events) == 1
    assert events[0].interrupted is False
    assert events[0].playback_position == pytest.approx(0.1)


async def test_clear_buffer_does_not_emit_playback_finished_itself() -> None:
    """계약 3. clear_buffer 가 직접 쏘면 flush 태스크와 이중 발화가 된다."""
    call = _make_call(_FakeMediaWs(playout_delay=10.0))
    out = ClawOpsAudioOutput(call)
    events: list[io.PlaybackFinishedEvent] = []
    out.on("playback_finished", events.append)

    await out.capture_frame(_pcm_frame(8000))  # 1초
    out.clear_buffer()
    await asyncio.sleep(0.05)

    # flush 가 없었으므로 대기 태스크 자체가 없다 -> 아무것도 안 나가야 한다.
    assert events == []
    call._send_clear_fn.assert_awaited_once()


async def test_barge_in_reports_partial_playback_position() -> None:
    call = _make_call(_FakeMediaWs(playout_delay=10.0))
    out = ClawOpsAudioOutput(call)
    events: list[io.PlaybackFinishedEvent] = []
    out.on("playback_finished", events.append)

    await out.capture_frame(_pcm_frame(80000))  # 10초 분량을 즉시 밀어넣음
    out.flush()
    await asyncio.sleep(0.1)  # 0.1초만 "재생"
    out.clear_buffer()
    await asyncio.sleep(0.05)

    assert len(events) == 1
    assert events[0].interrupted is True
    # 10초를 밀어넣었지만 실제로 들린 건 0.1초 남짓이어야 한다.
    assert 0.0 < events[0].playback_position < 1.0


async def test_mark_is_sent_after_local_queue_flush() -> None:
    """send_mark 는 WS 로 즉시 나가고 send_audio 는 큐에 쌓인다 —
    ws.flush() 를 먼저 안 하면 mark 가 오디오를 추월한다."""
    ws = _FakeMediaWs()
    call = _make_call(ws)
    out = ClawOpsAudioOutput(call)

    await out.capture_frame(_pcm_frame(800))
    out.flush()
    await asyncio.sleep(0.05)

    # 카운트가 아니라 순서를 검증한다 — flush 가 mark 보다 먼저여야 한다.
    assert ws.ops == ["flush", "mark"]


# ── 계약 1: TranscriptSynchronizer (이 파일의 핵심) ─────────────


class _NullInput(io.AudioInput):
    def __init__(self) -> None:
        super().__init__(label="null")

    async def __anext__(self) -> rtc.AudioFrame:
        await asyncio.sleep(3600)
        raise StopAsyncIteration


_FULL = "This is a very long assistant sentence that the user will barge in on halfway through."


async def _run_barge_in(*, with_sync: bool) -> str | None:
    """say() 로 10초 분량을 재생하다 0.3초 만에 끊고, 히스토리에 남은 텍스트를 반환."""
    call = _make_call(_FakeMediaWs(playout_delay=10.0))
    out = ClawOpsAudioOutput(call)

    session: AgentSession = AgentSession(stt=None, llm=None, tts=None, vad=None, turn_detection=None)
    if with_sync:
        sync = TranscriptSynchronizer(next_in_chain_audio=out, next_in_chain_text=None)
        session.output.audio = sync.audio_output
        session.output.transcription = sync.text_output
    else:
        session.output.audio = out
    session.input.audio = _NullInput()

    await session.start(Agent(instructions="test"))

    async def _text():
        for word in _FULL.split(" "):
            yield word + " "

    async def _audio():
        for _ in range(100):
            yield _pcm_frame(800)  # 100 * 100ms == 10초
            # 이벤트 루프에 틈을 준다. 실제로는 TTS 오디오가 네트워크를 타고
            # 점진적으로 도착하므로 자연히 생기는 틈이다.
            #
            # ⚠️ 이 yield 가 없으면 TranscriptSynchronizer 가 텍스트 스트림을
            # 처리하기 전에 오디오가 전부 밀려들어가 synchronized_transcript 가
            # 빈 문자열이 되고, agent_activity.py 의 `if forwarded_text` 가
            # falsy 라 assistant 메시지가 통째로 유실된다 (LiveKit 동작).
            await asyncio.sleep(0)

    session.say(_text(), audio=_audio())
    await asyncio.sleep(0.3)
    await session.interrupt()
    await asyncio.sleep(0.3)

    stored = None
    for item in session.history.items:
        if getattr(item, "role", None) == "assistant":
            stored = item.text_content
    await session.aclose()
    return stored


async def test_barge_in_truncates_history() -> None:
    """계약 1. TranscriptSynchronizer 로 감싸면 실제 들린 만큼만 컨텍스트에 남는다."""
    stored = await _run_barge_in(with_sync=True)

    assert stored is not None, "assistant 메시지가 통째로 사라졌다 — on_playback_started 누락?"
    assert stored.strip(), "빈 문자열 — first_frame_fut 이 resolve 되지 않았다"
    assert _FULL.startswith(stored.strip()), f"들린 텍스트의 접두사가 아니다: {stored!r}"
    assert len(stored.strip()) < len(_FULL), (
        f"전체 텍스트가 그대로 남았다 — 모델이 하지 않은 말을 했다고 믿게 된다: {stored!r}"
    )


async def test_barge_in_without_synchronizer_keeps_full_text() -> None:
    """계약 1의 반대 증명 — 감싸지 않으면 전체 텍스트가 남는다(= 우리가 피하려는 버그).

    이 테스트가 깨진다면 LiveKit 이 룸 없이도 정렬을 해주게 되었다는 뜻이므로,
    그때는 _session.py 의 TranscriptSynchronizer 래핑을 재검토할 것.
    """
    stored = await _run_barge_in(with_sync=False)

    assert stored is not None
    assert stored.strip() == _FULL.strip()


# ── 회귀: 세그먼트 회계 / prewarm 정리 ─────────────────────────


class _BufferCall:
    """_BufferingCall 처럼 clear_audio 를 no-op 으로만 갖는 prewarm 스텁."""

    def __init__(self) -> None:
        self.cleared = 0

    async def send_audio(self, chunk: bytes) -> None:
        pass

    async def clear_audio(self) -> None:
        self.cleared += 1


async def test_clear_buffer_during_prewarm_does_not_raise() -> None:
    """prewarm 중(_BufferingCall) clear_buffer 가 AttributeError 를 내면 안 된다."""
    out = ClawOpsAudioOutput(_BufferCall())
    await out.capture_frame(_pcm_frame(800))
    out.clear_buffer()  # _BufferingCall.clear_audio 가 없으면 여기서 터졌었다
    await asyncio.sleep(0.01)


async def test_sequential_segments_each_emit_finish_once() -> None:
    """연속 세그먼트가 각각 정확히 한 번씩 완료돼야 한다.

    이전엔 flush 가 진행 중인 flush 태스크를 cancel 해서 세그먼트가 유실됐고,
    프레임워크 wait_for_playout 이 영구 대기했다. 이제는 취소 대신 직렬화한다.
    """
    call = _make_call(_FakeMediaWs())  # mark 즉시 echo
    out = ClawOpsAudioOutput(call)
    events: list[io.PlaybackFinishedEvent] = []
    out.on("playback_finished", events.append)

    for _ in range(3):
        await out.capture_frame(_pcm_frame(800))
        out.flush()
        await asyncio.sleep(0.02)

    assert len(events) == 3
    assert all(not e.interrupted for e in events)


async def test_close_cancels_pending_flush_and_still_emits() -> None:
    """close() 는 대기 중인 flush 태스크를 정리하되 세그먼트는 닫아야 한다."""
    call = _make_call(_FakeMediaWs(playout_delay=10.0))
    out = ClawOpsAudioOutput(call)
    events: list[io.PlaybackFinishedEvent] = []
    out.on("playback_finished", events.append)

    await out.capture_frame(_pcm_frame(800))
    out.flush()
    await asyncio.sleep(0.01)  # mark 대기 진입
    out.close()
    await asyncio.sleep(0.02)

    # 취소돼도 on_playback_finished 는 한 번 나가야 한다 (count 어긋남 방지)
    assert len(events) == 1
