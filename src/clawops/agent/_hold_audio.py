"""Tool 실행 중 대기 오디오 재생.

HoldAudioPlayer는 tool 실행 동안 caller에게 대기음을 루프 재생한다.
"""

from __future__ import annotations

import asyncio
import logging
import math
import struct
import wave
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from ._audio import pcm16_to_ulaw, resample_pcm16

if TYPE_CHECKING:
    from ._session import CallSession

log = logging.getLogger("clawops.agent")

CHUNK_SIZE = 160  # 20ms @ 8kHz ulaw (1 byte per sample)
SAMPLE_RATE = 8000


def _bell_note(freq: float, duration_ms: int = 500, volume: float = 0.12) -> list[int]:
    """벨/차임 스타일 단일 음 생성 (inharmonic partials + exponential decay)."""
    n = SAMPLE_RATE * duration_ms // 1000
    attack_samples = int(0.003 * SAMPLE_RATE)  # 3ms attack (클릭 방지)
    partials = [
        # (freq_ratio, amplitude, decay_rate)
        (1.0, 1.0, 1.2),
        (2.76, 0.5, 2.5),
        (5.4, 0.25, 4.0),
    ]
    samples = []
    for i in range(n):
        t = i / SAMPLE_RATE
        val = 0.0
        for freq_ratio, amp, decay in partials:
            f = freq * freq_ratio
            if f >= SAMPLE_RATE / 2:
                continue  # Nyquist 초과 방지
            env = amp * math.exp(-decay * t * (1000 / duration_ms))
            val += env * math.sin(2 * math.pi * f * t)
        # attack fade-in
        if i < attack_samples:
            val *= i / attack_samples
        samples.append(int(volume * 32767 * max(-1.0, min(1.0, val))))
    return samples


def _silence(duration_ms: int) -> list[int]:
    """무음 샘플 생성."""
    return [0] * (SAMPLE_RATE * duration_ms // 1000)


# C5 펜타토닉 스케일 (뮤직박스/차임 스타일)
_PENTATONIC_C5 = {
    "C5": 523.25,
    "D5": 587.33,
    "E5": 659.25,
    "G5": 783.99,
    "A5": 880.00,
    "C6": 1046.50,
}


def generate_comfort_tone(volume: float = 0.12) -> list[bytes]:
    """뮤직박스 스타일 대기음 생성 (C5 펜타토닉 차임 멜로디 + 여백).

    약 10초 길이의 멜로디가 루프 재생된다.

    Args:
        volume: 볼륨 (0.0 ~ 1.0). 기본값은 낮게 설정.
    """
    p = _PENTATONIC_C5

    # 멜로디 패턴: ascending chime → gentle descent
    melody = [
        (p["E5"], 450),
        (p["G5"], 450),
        (p["A5"], 450),
        (p["C6"], 600),
        # 짧은 쉼
        (0, 800),
        (p["A5"], 400),
        (p["G5"], 400),
        (p["E5"], 400),
        (p["D5"], 600),
        # 마무리 쉼
        (0, 2500),
        (p["C5"], 500),
        (p["E5"], 500),
        (p["C6"], 700),
        # 루프 전 긴 여백
        (0, 2500),
    ]

    pcm_samples: list[int] = []
    for freq, dur_ms in melody:
        if freq == 0:
            pcm_samples.extend(_silence(dur_ms))
        else:
            pcm_samples.extend(_bell_note(freq, dur_ms, volume))
            pcm_samples.extend(_silence(150))  # 음 사이 간격

    pcm = struct.pack(f"<{len(pcm_samples)}h", *pcm_samples)
    ulaw = pcm16_to_ulaw(pcm)

    return [ulaw[i : i + CHUNK_SIZE] for i in range(0, len(ulaw), CHUNK_SIZE)]


def load_hold_audio(source: bool | str | bytes) -> list[bytes]:
    """설정값에 따라 hold audio 청크를 로드한다.

    Args:
        source: True → 기본 comfort tone, str → wav 파일 경로, bytes → raw ulaw 데이터.
    """
    if source is True:
        return generate_comfort_tone()

    if isinstance(source, bytes):
        return [source[i : i + CHUNK_SIZE] for i in range(0, len(source), CHUNK_SIZE)]

    if isinstance(source, str):
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Hold audio 파일을 찾을 수 없습니다: {source}")

        with wave.open(str(path), "rb") as wf:
            n_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            frame_rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())

        if sample_width != 2:
            raise ValueError(f"16-bit PCM wav만 지원합니다 (현재: {sample_width * 8}-bit)")

        # 스테레오 → 모노 (왼쪽 채널만 사용)
        if n_channels == 2:
            samples = struct.unpack(f"<{len(frames) // 2}h", frames)
            mono = samples[::2]
            frames = struct.pack(f"<{len(mono)}h", *mono)

        # 리샘플링 → 8kHz
        if frame_rate != SAMPLE_RATE:
            frames = resample_pcm16(frames, from_rate=frame_rate, to_rate=SAMPLE_RATE)

        ulaw = pcm16_to_ulaw(frames)
        return [ulaw[i : i + CHUNK_SIZE] for i in range(0, len(ulaw), CHUNK_SIZE)]

    raise TypeError(f"지원하지 않는 hold_audio 타입: {type(source)}")


# 발신자에게 키를 받는 도구. 이게 끼어 있는 묶음엔 대기음을 틀지 않는다 — 대기음이 안내를
# 덮고, 송신 큐가 영영 안 비어 collect_dtmf 가 안내 재생 끝을 기다리다 상한까지 간다.
_NO_HOLD_AUDIO_TOOLS = frozenset({"collect_dtmf"})


def hold_audio_allowed(tool_names: Iterable[str]) -> bool:
    """이 도구 묶음을 실행하는 동안 대기음을 틀어도 되는가."""
    return not any(name in _NO_HOLD_AUDIO_TOOLS for name in tool_names)


class HoldAudioPlayer:
    """Tool 실행 중 대기 오디오를 루프 재생한다."""

    def __init__(self, call: CallSession, audio_chunks: list[bytes]) -> None:
        self._call = call
        self._chunks = audio_chunks
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._play_loop())
        log.debug("Hold audio started")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        await self._call.clear_audio()
        log.debug("Hold audio stopped")

    async def _play_loop(self) -> None:
        try:
            while True:
                for chunk in self._chunks:
                    await self._call.send_audio(chunk)
                    await asyncio.sleep(0.02)  # 20ms pacing
        except asyncio.CancelledError:
            return
