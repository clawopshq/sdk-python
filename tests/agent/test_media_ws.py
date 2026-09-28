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


class _RecordingSession:
    """ws_connect 에 넘어온 인자를 기록하고, 곧바로 닫힌 소켓을 돌려준다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    async def ws_connect(self, url, **kwargs):
        self.calls.append((url, kwargs))

        class _Closed:
            closed = True

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            async def close(self):
                return None

        return _Closed()

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_connect_sends_no_authorization_header(monkeypatch):
    """clawops#1250: 미디어 WS 인증은 URL token 뿐이다 — 계정 API 키를 헤더로 싣지 않는다."""
    import clawops.agent._media_ws as media_ws_mod

    session = _RecordingSession()
    monkeypatch.setattr(media_ws_mod.aiohttp, "ClientSession", lambda: session)

    ws = MediaWebSocket(
        url="wss://api.claw-ops.com/v1/agent/media/CA1?token=t",
        api_key="sk_live_should_not_be_sent",
        on_audio=AsyncMock(),
        on_start=AsyncMock(),
        on_stop=AsyncMock(),
    )
    try:
        await ws.connect()
    except Exception:
        pass  # 수신 루프 이후 경로는 이 테스트의 관심사가 아니다

    assert len(session.calls) == 1
    url, kwargs = session.calls[0]
    assert url == "wss://api.claw-ops.com/v1/agent/media/CA1?token=t"
    headers = kwargs.get("headers") or {}
    assert not any(k.lower() == "authorization" for k in headers)


def test_api_key_is_optional():
    ws = MediaWebSocket(url="wss://x/v1/agent/media/CA1?token=t", on_audio=AsyncMock(), on_start=AsyncMock(), on_stop=AsyncMock())
    assert not hasattr(ws, "_api_key")


def test_plaintext_public_url_warns(caplog):
    from clawops.agent._media_ws import _warn_if_plaintext

    with caplog.at_level("WARNING", logger="clawops.agent"):
        _warn_if_plaintext("ws://api.claw-ops.com/v1/agent/media/CA1?token=t")
    assert any("plaintext" in r.message for r in caplog.records)
    assert not any("token=t" in r.getMessage() for r in caplog.records)  # 토큰을 로그에 흘리지 않는다

    caplog.clear()
    with caplog.at_level("WARNING", logger="clawops.agent"):
        _warn_if_plaintext("ws://localhost:3100/v1/agent/media/CA1?token=t")
        _warn_if_plaintext("wss://api.claw-ops.com/v1/agent/media/CA1?token=t")
    assert caplog.records == []


@pytest.mark.asyncio
async def test_connected_log_omits_token(monkeypatch, caplog):
    """연결 로그에 URL 의 1회용 token(query)을 남기지 않는다."""
    import clawops.agent._media_ws as media_ws_mod

    session = _RecordingSession()
    monkeypatch.setattr(media_ws_mod.aiohttp, "ClientSession", lambda: session)
    ws = MediaWebSocket(
        url="wss://api.claw-ops.com/v1/agent/media/CA1?token=secret-token-123&node=n1#frag",
        on_audio=AsyncMock(),
        on_start=AsyncMock(),
        on_stop=AsyncMock(),
    )
    with caplog.at_level("DEBUG", logger="clawops.agent"):
        try:
            await ws.connect()
        except Exception:
            pass
    messages = [r.getMessage() for r in caplog.records]
    assert any("wss://api.claw-ops.com/v1/agent/media/CA1" in m for m in messages)
    assert not any("secret-token-123" in m or "token=" in m or "frag" in m for m in messages)
