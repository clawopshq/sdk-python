# tests/agent/test_media_ws.py
import base64
from unittest.mock import AsyncMock

import pytest
from clawops.agent._media_ws import (
    MediaWebSocket,
    build_media_response,
    parse_media_event,
    parse_start_event,
)


def _fake_ws() -> MediaWebSocket:
    ws = MediaWebSocket(
        url="ws://x", api_key="k", on_audio=AsyncMock(), on_start=AsyncMock(), on_stop=AsyncMock()
    )

    class _Open:
        closed = False

    ws._ws = _Open()  # type: ignore[assignment]
    return ws


@pytest.mark.asyncio
async def test_wait_for_mark_timeout_does_not_raise_and_pops_waiter():
    """마크가 안 와도 wait_for_mark 는 raise 하지 않고 waiter 를 회수해야 한다.

    (예전 finally-only 버전은 TimeoutError 를 전파해 _graceful_hangup 과
    _race_interrupt 를 깨뜨렸다.)
    """
    ws = _fake_ws()

    await ws.wait_for_mark("m1", timeout=0.02)  # raise 하면 이 줄에서 실패

    assert "m1" not in ws._mark_waiters  # timeout 경로에서도 회수됨


@pytest.mark.asyncio
async def test_wait_for_mark_echo_resolves_and_pops():
    ws = _fake_ws()

    import asyncio

    async def echo():
        await asyncio.sleep(0.01)
        ws._mark_waiters["m2"].set()

    task = asyncio.create_task(echo())
    await ws.wait_for_mark("m2", timeout=1.0)
    await task
    assert "m2" not in ws._mark_waiters


def test_parse_media_event():
    pcm = b"\x00\x01" * 80
    event = {
        "event": "media",
        "media": {
            "track": "inbound",
            "chunk": "1",
            "timestamp": "100",
            "payload": base64.b64encode(pcm).decode(),
        },
    }
    result = parse_media_event(event)
    assert result["audio"] == pcm
    assert result["timestamp"] == 100


def test_build_media_response():
    pcm = b"\x00\x01" * 80
    msg = build_media_response(pcm)
    assert msg["event"] == "media"
    decoded = base64.b64decode(msg["media"]["payload"])
    assert decoded == pcm


def test_parse_start_event():
    event = {
        "event": "start",
        "start": {
            "streamId": "MZ_abc",
            "callId": "CA_123",
            "accountId": "AC_test",
            "tracks": ["inbound"],
            "mediaFormat": {"encoding": "audio/x-l16", "sampleRate": 8000, "channels": 1},
        },
    }
    result = parse_start_event(event)
    assert result["stream_id"] == "MZ_abc"
    assert result["call_id"] == "CA_123"
    assert result["sample_rate"] == 8000


def test_build_dtmf_message():
    """send_dtmf가 올바른 포맷의 JSON 메시지를 생성하는지 확인."""
    from clawops.agent._media_ws import build_dtmf_message
    msg = build_dtmf_message("5")
    assert msg == {"event": "dtmf", "dtmf": {"digit": "5"}}


def test_build_dtmf_message_invalid():
    """유효하지 않은 digit에 대해 ValueError를 발생시키는지 확인."""
    from clawops.agent._media_ws import build_dtmf_message
    with pytest.raises(ValueError, match="유효하지 않은 DTMF digit"):
        build_dtmf_message("A")


def test_parse_dtmf_event():
    """서버에서 수신한 DTMF 이벤트를 파싱하는지 확인."""
    from clawops.agent._media_ws import parse_dtmf_event
    data = {
        "event": "dtmf",
        "sequenceNumber": "5",
        "dtmf": {"digit": "1", "track": "inbound_track"},
    }
    result = parse_dtmf_event(data)
    assert result["digit"] == "1"
    assert result["track"] == "inbound_track"
