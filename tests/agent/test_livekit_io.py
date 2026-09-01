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
from clawops.agent.livekit import _io  # noqa: E402
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


async def test_output_sends_tail_raw_on_flush() -> None:
    """자투리를 무음으로 채우지 않고 그대로 내보낸다.

    채우면 발화 한가운데 0~19ms 구멍이 생긴다 — 이 시점의 우리는 뒤에 오디오가 더
    오는지 모르는 채로 세그먼트를 닫기 때문이다. 그 판단은 큐를 들고 있는 엔진 몫이고,
    엔진은 뒤따르는 mark 를 세그먼트 끝 신호로 읽어 그때 채운다.
    """
    call = _make_call(_FakeMediaWs())
    out = ClawOpsAudioOutput(call)

    await out.capture_frame(_pcm_frame(40))  # 160B 미만 — 자투리만
    assert _sent_chunks(call) == []

    out.flush()
    await asyncio.sleep(0.05)

    chunks = _sent_chunks(call)
    assert len(chunks) == 1
    # ⚠️ 내용으로는 판별할 수 없다 — 테스트 페이로드가 PCM 0 이라 μ-law 로도 0xff 다.
    #    패딩 여부는 **길이**로만 갈린다.
    assert len(chunks[0]) == 40, "자투리를 채우지 않고 그대로 내보내야 한다"


async def test_output_pads_tail_when_killswitch_set(monkeypatch) -> None:
    """`CLAWOPS_TAIL_PAD=1` 은 종전 동작으로 되돌린다 — 구 엔진으로 롤백할 때 쓴다."""
    monkeypatch.setattr(_io, "_TAIL_PAD", True)
    call = _make_call(_FakeMediaWs())
    out = ClawOpsAudioOutput(call)

    await out.capture_frame(_pcm_frame(40))
    out.flush()
    await asyncio.sleep(0.05)

    chunks = _sent_chunks(call)
    assert len(chunks) == 1
    assert len(chunks[0]) == FRAME_BYTES
    assert chunks[0][40:] == b"\xff" * (FRAME_BYTES - 40)


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

# _FULL(16단어)을 10초에 걸쳐 읽으므로 TranscriptSynchronizer 는 단어당 약 0.6초를 쓴다.
# 끊기 전에 이만큼 재생해야 "들린 만큼"이 최소 두어 단어가 된다.
_HEARD_BEFORE_INTERRUPT = 2.0


async def _wait_for(predicate, *, timeout: float = 5.0) -> bool:
    """predicate 가 참이 될 때까지 폴링한다. 벽시계 sleep 대신 쓴다."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


async def _run_barge_in(*, with_sync: bool) -> str | None:
    """say() 로 10초 분량을 재생하다 도중에 끊고, 히스토리에 남은 텍스트를 반환."""
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

    # ⚠️ 고정 sleep 으로 끊으면 안 된다. 동기화기는 재생 시작 시각을 기준으로 텍스트를
    # 흘려보내므로, 첫 프레임이 늦게 잡히면 끊는 시점에 전달된 단어가 0개가 되고
    # LiveKit 은 빈 assistant 메시지를 통째로 버린다(= stored is None). 실제로
    # 스위트 전체를 돌릴 때만 그렇게 깨졌다 — 0.3초는 단어 하나의 절반이라 여유가 없다.
    assert await _wait_for(lambda: bool(_sent_chunks(call))), "재생이 시작되지 않았다"
    await asyncio.sleep(_HEARD_BEFORE_INTERRUPT)
    await session.interrupt()

    def _assistant() -> str | None:
        stored = None
        for item in session.history.items:
            if getattr(item, "role", None) == "assistant":
                stored = item.text_content
        return stored

    await _wait_for(lambda: _assistant() is not None)
    stored = _assistant()
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
