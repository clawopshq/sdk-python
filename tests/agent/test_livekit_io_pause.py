"""ClawOpsAudioOutput 일시정지(pause/resume) 계약 테스트 — `_io.py` 모듈 docstring 「일시정지」.

LiveKit 은 발신자 음성이 감지되면 `pause()`, 오탐이면 `resume()` 을 부른다. 우리 오디오는
엔진 페이서 큐에 세그먼트째 들어가 있으므로 pause 는 큐 clear + 위치 기억, resume 은 그
위치(살짝 앞)부터 재전송이다. 여기서 고정하는 것:

1. `pause=True` 일 때만 `can_pause` — 기본은 종전과 같다(끼어들기 동작 불변).
2. pause 가 엔진 큐를 비우고, resume 이 되감은 위치부터 남은 바이트를 다시 보낸다.
3. ⚠️ 엔진은 clear 를 받으면 대기 중인 mark 를 **즉시 되돌려 준다**(call-handler.js). 그걸
   재생 완료로 읽으면 세그먼트가 일시정지 중에 닫힌다 — 무효 mark 를 걸러야 한다.
4. 일시정지 중 진짜 끊김(clear_buffer)의 `playback_position` 은 멈춘 자리다(경과 시간이 아니라).
5. 첫 프레임 전에 pause 되면 오디오를 들고 있다가 resume 에 내보낸다(LiveKit 의
   `_reconcile_playout_pause` 경로).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

livekit_agents = pytest.importorskip("livekit.agents", reason="clawops[livekit] 미설치")

from livekit import rtc  # noqa: E402
from livekit.agents.voice import io  # noqa: E402
from livekit.agents.voice.transcription import TranscriptSynchronizer  # noqa: E402

from clawops.agent._session import CallSession  # noqa: E402
from clawops.agent.livekit._io import (  # noqa: E402
    FRAME_BYTES,
    SAMPLE_RATE,
    ClawOpsAudioOutput,
)


class _EngineWs:
    """엔진 동작을 흉내내는 Media WS 스텁.

    - mark 는 명시적으로 `echo()` 하기 전엔 돌아오지 않는다(재생 중).
    - `clear()` 는 **대기 중인 mark 를 전부 즉시 되돌린다** — call-handler.js 의 실제 동작.
    """

    def __init__(self) -> None:
        self.is_connected = True
        self.pending: list[str] = []
        self.echoed: set[str] = set()
        self.ops: list[str] = []

    async def flush(self) -> None:
        self.ops.append("flush")

    async def send_mark(self, name: str) -> None:
        self.pending.append(name)
        self.ops.append("mark")

    async def wait_for_mark(self, name: str, timeout: float = 5.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while name not in self.echoed and loop.time() < deadline:
            await asyncio.sleep(0.005)

    async def clear(self) -> None:
        self.ops.append("clear")
        self.echo()

    def echo(self) -> None:
        for n in self.pending:
            self.echoed.add(n)
        self.pending.clear()


def _make_call(ws: _EngineWs) -> CallSession:
    call = CallSession(
        call_id="test-call",
        from_number="01012345678",
        to_number="07012341234",
        account_id="AC123",
    )
    call._send_audio_fn = AsyncMock()
    call._send_clear_fn = AsyncMock(side_effect=ws.clear)
    call._media_ws = ws
    return call


def _pcm_frame(samples: int) -> rtc.AudioFrame:
    return rtc.AudioFrame(
        data=b"\x00\x00" * samples,
        sample_rate=SAMPLE_RATE,
        num_channels=1,
        samples_per_channel=samples,
    )


def _sent(call: CallSession) -> list[bytes]:
    return [c.args[0] for c in call._send_audio_fn.await_args_list]


async def _wait_for(predicate, *, timeout: float = 2.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return False


# ── 1. 능력 플래그 ──────────────────────────────────────────────


async def test_can_pause_only_when_enabled() -> None:
    ws = _EngineWs()
    assert ClawOpsAudioOutput(_make_call(ws)).can_pause is False
    out = ClawOpsAudioOutput(_make_call(ws), pause=True)
    assert out.can_pause is True
    # TranscriptSynchronizer 로 감싸도 능력이 체인을 타고 올라온다(LiveKit 이 보는 건 이쪽).
    sync = TranscriptSynchronizer(next_in_chain_audio=out, next_in_chain_text=None)
    assert sync.audio_output.can_pause is True


async def test_pause_resume_noop_when_disabled_or_not_paused() -> None:
    ws = _EngineWs()
    call = _make_call(ws)
    out = ClawOpsAudioOutput(call, pause=True)
    out.resume()  # pause 없이 resume — 아무 일도 없어야 한다
    await asyncio.sleep(0.01)
    assert _sent(call) == []
    call._send_clear_fn.assert_not_awaited()


# ── 2. pause = clear + 위치 기억, resume = 되감은 위치부터 재전송 ───────


async def test_pause_clears_engine_and_resume_resends_from_rewound_position() -> None:
    ws = _EngineWs()
    call = _make_call(ws)
    out = ClawOpsAudioOutput(call, pause=True)

    await out.capture_frame(_pcm_frame(SAMPLE_RATE * 2))  # 2초 = 100 프레임, 즉시 큐잉
    out.flush()
    assert len(_sent(call)) == 100
    await asyncio.sleep(0.30)  # 0.3초 "재생"

    out.pause()
    await asyncio.sleep(0.02)
    call._send_clear_fn.assert_awaited_once()
    assert len(_sent(call)) == 100  # pause 자체는 아무것도 보내지 않는다

    out.resume()
    assert await _wait_for(lambda: len(_sent(call)) > 100)
    await asyncio.sleep(0.05)
    resent = _sent(call)[100:]
    # 재개 지점 ≈ (0.30 - 0.12초 되감기) * 8000 = 1440B → 남은 (16000-1440)/160 = 91 프레임.
    # 시계 오차를 감안해 폭을 둔다 — 핵심은 "처음부터 다시" 도 "끝에서부터" 도 아니라는 것.
    assert 80 <= len(resent) <= 96, len(resent)
    assert all(len(c) == FRAME_BYTES for c in resent)


# ── 3. clear 가 되돌린 옛 mark 는 재생 완료가 아니다 ─────────────────


async def test_stale_mark_echoed_by_clear_does_not_finish_segment() -> None:
    ws = _EngineWs()
    call = _make_call(ws)
    out = ClawOpsAudioOutput(call, pause=True)
    events: list[io.PlaybackFinishedEvent] = []
    out.on("playback_finished", events.append)

    await out.capture_frame(_pcm_frame(SAMPLE_RATE))  # 1초
    out.flush()
    assert await _wait_for(lambda: len(ws.pending) == 1)  # mark1 이 엔진에 걸렸다
    await asyncio.sleep(0.1)

    out.pause()  # → clear → 엔진이 mark1 을 즉시 되돌린다
    await asyncio.sleep(0.05)
    assert "clear" in ws.ops
    assert events == [], "clear 가 되돌린 mark 를 재생 완료로 읽었다 — 일시정지 중에 세그먼트가 닫힌다"

    out.resume()
    assert await _wait_for(lambda: len(ws.pending) == 1)  # 재전송 뒤 새 mark2
    assert events == []
    ws.echo()  # 이제 진짜 재생 완료
    assert await _wait_for(lambda: len(events) == 1)
    assert events[0].interrupted is False
    assert events[0].playback_position == pytest.approx(1.0)
    # 순서: mark1 → clear → (재전송) → flush → mark2. mark2 는 재전송 뒤에 서야 한다.
    assert ws.ops.index("clear") < len(ws.ops) - 1
    assert ws.ops[-2:] == ["flush", "mark"]


# ── 4. 일시정지 중 진짜 끊김 → 멈춘 자리가 playback_position ───────────


async def test_interrupt_while_paused_reports_pause_position() -> None:
    ws = _EngineWs()
    call = _make_call(ws)
    out = ClawOpsAudioOutput(call, pause=True)
    events: list[io.PlaybackFinishedEvent] = []
    out.on("playback_finished", events.append)

    await out.capture_frame(_pcm_frame(SAMPLE_RATE * 10))  # 10초
    out.flush()
    await asyncio.sleep(0.2)
    out.pause()
    await asyncio.sleep(0.3)  # 멈춘 채 0.3초 — 이 시간은 "들린" 게 아니다
    out.clear_buffer()  # 진짜 끊김(LiveKit 이 전사로 확정)
    assert await _wait_for(lambda: len(events) == 1)

    assert events[0].interrupted is True
    assert 0.05 < events[0].playback_position < 0.4, events[0].playback_position


# ── 5. 첫 프레임 전에 pause 되면 들고 있다가 resume 에 내보낸다 ────────


async def test_pause_before_first_frame_holds_audio_until_resume() -> None:
    ws = _EngineWs()
    call = _make_call(ws)
    out = ClawOpsAudioOutput(call, pause=True)
    events: list[io.PlaybackFinishedEvent] = []
    out.on("playback_finished", events.append)

    out.pause()  # SOS 가 먼저 왔다(_reconcile_playout_pause)
    await out.capture_frame(_pcm_frame(4000))  # 0.5초
    out.flush()
    await asyncio.sleep(0.05)
    assert _sent(call) == []  # 아직 아무것도 안 나갔다
    assert events == []
    call._send_clear_fn.assert_not_awaited()  # 보낸 게 없으니 clear 도 없다

    out.resume()
    assert await _wait_for(lambda: len(_sent(call)) == 25)
    assert await _wait_for(lambda: len(ws.pending) == 1)
    ws.echo()
    assert await _wait_for(lambda: len(events) == 1)
    assert events[0].interrupted is False
    assert events[0].playback_position == pytest.approx(0.5)


async def test_pause_state_persists_into_next_segment() -> None:
    """LiveKit 은 pause 를 싱크 수준 상태로 본다 — 세그먼트가 바뀌어도 resume 전엔 내보내지 않는다."""
    ws = _EngineWs()
    call = _make_call(ws)
    out = ClawOpsAudioOutput(call, pause=True)
    events: list[io.PlaybackFinishedEvent] = []
    out.on("playback_finished", events.append)

    await out.capture_frame(_pcm_frame(SAMPLE_RATE))
    out.flush()
    await asyncio.sleep(0.05)
    out.pause()
    out.clear_buffer()  # 첫 세그먼트는 끊겼다
    assert await _wait_for(lambda: len(events) == 1)
    sent_before = len(_sent(call))

    await out.capture_frame(_pcm_frame(1600))  # 다음 세그먼트 — 아직 paused
    await asyncio.sleep(0.02)
    assert len(_sent(call)) == sent_before

    out.resume()
    assert await _wait_for(lambda: len(_sent(call)) == sent_before + 10)
