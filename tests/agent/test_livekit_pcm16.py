"""16kHz must preserve real >4kHz content, timing, and pause/mark fences."""

from __future__ import annotations

import asyncio
import math
import struct

import pytest

pytest.importorskip("livekit.agents")
from livekit import rtc
from livekit.agents import Agent, AgentSession

from clawops.agent.livekit import LiveKitSession, _io
from clawops.agent.livekit._io import ClawOpsAudioInput, ClawOpsAudioOutput
from clawops.agent.media import MediaProfile
from tests.agent.test_livekit_io import _FakeMediaWs, _make_call, _sent_chunks

P = MediaProfile.PCM_S16LE_16000


def frame(pcm):
    return rtc.AudioFrame(data=pcm, sample_rate=16000, num_channels=1, samples_per_channel=len(pcm) // 2)


def tone(seconds=1):
    samples = [round(10000 * math.sin(2 * math.pi * 5000 * i / 16000)) for i in range(round(16000 * seconds))]
    return struct.pack(f"<{len(samples)}h", *samples)


async def test_one_second_5khz_roundtrip_is_bit_exact_without_downsampling():
    pcm = tone()
    call = _make_call(_FakeMediaWs())
    out = ClawOpsAudioOutput(call, media_profile=P)
    src = ClawOpsAudioInput(media_profile=P)
    events = []
    out.on("playback_finished", events.append)
    try:
        assert out.sample_rate == 16000
        await out.capture_frame(frame(pcm))
        out.flush()
        await out._flush_task
        chunks = _sent_chunks(call)
        assert len(chunks) == 50
        assert all(len(c) == 640 for c in chunks)
        assert b"".join(chunks) == pcm  # preserves 5kHz amplitude, phase, endian and length
        received = bytearray()
        duration = 0.0
        for chunk in chunks:
            src.push_audio(chunk)
            f = await src.__anext__()
            assert f.sample_rate == 16000
            assert f.num_channels == 1
            received += bytes(f.data)
            duration += f.duration
        assert received == pcm
        assert duration == pytest.approx(1)
        assert len(events) == 1 and not events[0].interrupted
        assert events[0].playback_position == pytest.approx(1)
    finally:
        out.close()


@pytest.mark.parametrize("pad", [False, True])
async def test_partial_sample_aligned_tail_uses_pcm_silence(monkeypatch, pad):
    monkeypatch.setattr(_io, "_TAIL_PAD", pad)
    call = _make_call(_FakeMediaWs())
    out = ClawOpsAudioOutput(call, media_profile=P)
    try:
        pcm = tone()[:642]
        await out.capture_frame(frame(pcm))
        assert list(map(len, _sent_chunks(call))) == [640]
        out.flush()
        await out._flush_task
        expected = pcm + (b"\0" * 638 if pad else b"")
        assert b"".join(_sent_chunks(call)) == expected
    finally:
        out.close()


async def test_pause_resume_rewinds_by_time_not_byte_rate(monkeypatch):
    # Freeze playout at 500ms: 16000 bytes, not 8000 samples or 500ms/4.
    now = [100.0]
    monkeypatch.setattr(_io.time, "monotonic", lambda: now[0])
    call = _make_call(_FakeMediaWs())
    out = ClawOpsAudioOutput(call, pause=True, media_profile=P)
    pcm = tone()
    try:
        await out.capture_frame(frame(pcm))
        now[0] += 0.5
        assert out._estimate_played_bytes() == 16000
        out.pause()
        assert out._pause_pos == 16000
        assert out._resume_from == 12160  # (500ms - 120ms rewind) * 32000 B/s
        n = len(_sent_chunks(call))
        out.resume()
        await asyncio.sleep(0)
        assert b"".join(_sent_chunks(call)[n:]) == pcm[12160:]
    finally:
        out.close()


async def test_pcm_input_rejects_odd_bytes_and_legacy_entry_point():
    src = ClawOpsAudioInput(media_profile=P)
    with pytest.raises(ValueError, match="complete samples"):
        src.push_audio(b"\0")
    with pytest.raises(ValueError, match="push_ulaw"):
        src.push_ulaw(b"\xff" * 160)


@pytest.mark.parametrize("rate,channels", [(8000, 1), (16000, 2)])
async def test_wrong_livekit_frame_rejected_before_playback(rate, channels):
    call = _make_call()
    out = ClawOpsAudioOutput(call, media_profile=P)
    try:
        bad = rtc.AudioFrame(
            data=b"\0" * 640 * channels, sample_rate=rate, num_channels=channels, samples_per_channel=320
        )
        with pytest.raises(ValueError):
            await out.capture_frame(bad)
        assert not _sent_chunks(call)
        assert out._pushed_duration == 0
    finally:
        out.close()


async def test_prewarm_and_attach_keep_same_pcm_profile():
    async def create(call):
        return AgentSession(stt=None, llm=None, tts=None, vad=None, turn_detection=None), Agent(instructions="test")

    session = LiveKitSession(create, media_profile=P)
    try:
        await session.prewarm()
        assert session._output.sample_rate == 16000
        pcm = tone(0.02)
        await session._output.capture_frame(frame(pcm))
        call = _make_call(_FakeMediaWs())
        await session.attach(call)
        assert b"".join(_sent_chunks(call)) == pcm
        await session.feed_audio(pcm, 0)
        f = await session._input.__anext__()
        assert bytes(f.data) == pcm and f.sample_rate == 16000
    finally:
        await session.stop()
