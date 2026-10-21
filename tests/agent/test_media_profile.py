"""Per-call media contracts, including wideband content and playout accounting."""

from __future__ import annotations

import pytest

from clawops.agent._media_ws import parse_start_event
from clawops.agent.media import MediaProfile


@pytest.mark.parametrize(
    "profile,rate,size,silence",
    [
        (MediaProfile.MULAW_8000, 8000, 160, b"\xff"),
        (MediaProfile.PCM_S16LE_16000, 16000, 640, b"\0"),
    ],
)
def test_wire_contract(profile, rate, size, silence):
    assert profile.sample_rate == rate
    assert profile.frame_bytes == size
    assert profile.frame_bytes / profile.bytes_per_second == 0.02
    assert profile.silence_byte == silence
    profile.validate_start({"encoding": profile.encoding, "sample_rate": rate, "channels": 1})


def test_missing_start_format_is_legacy_only():
    info = parse_start_event({"start": {"streamId": "s", "callId": "c"}})
    MediaProfile.MULAW_8000.validate_start(info)
    with pytest.raises(ValueError, match="does not match"):
        MediaProfile.PCM_S16LE_16000.validate_start(info)


@pytest.mark.parametrize("field,value", [("encoding", "audio/L16"), ("sample_rate", 8000), ("channels", 2)])
def test_format_mismatch_rejected(field, value):
    p = MediaProfile.PCM_S16LE_16000
    info = {"encoding": p.encoding, "sample_rate": 16000, "channels": 1}
    info[field] = value
    with pytest.raises(ValueError):
        p.validate_start(info)


def test_unknown_profile_rejected():
    with pytest.raises(ValueError):
        MediaProfile("pcm_48000")


@pytest.mark.parametrize("matching", [True, False])
async def test_websocket_gates_prewarm_audio_until_start_format_is_validated(matching):
    import asyncio
    import json
    from unittest.mock import AsyncMock

    from aiohttp import WSMsgType, web

    from clawops.agent._media_ws import MediaWebSocket

    observed = []

    async def handler(request):
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        # Already queued PCM must not escape into an old μ-law server.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(socket.receive(), 0.03)
        profile = MediaProfile.PCM_S16LE_16000 if matching else MediaProfile.MULAW_8000
        await socket.send_json(
            {
                "event": "start",
                "start": {
                    "streamId": "s",
                    "callId": "c",
                    "mediaFormat": {
                        "encoding": profile.encoding,
                        "sampleRate": profile.sample_rate,
                        "channels": 1,
                    },
                },
            }
        )
        msg = await socket.receive()
        if msg.type == WSMsgType.TEXT:
            observed.append(json.loads(msg.data))
            await socket.send_json({"event": "stop"})
        await socket.close()
        return socket

    app = web.Application()
    app.router.add_get("/media", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    on_start = AsyncMock()
    ws = MediaWebSocket(
        url=f"ws://127.0.0.1:{port}/media",
        on_audio=AsyncMock(),
        on_start=on_start,
        on_stop=AsyncMock(),
        expected_media_profile=MediaProfile.PCM_S16LE_16000,
    )
    try:
        await ws.send_audio(b"\x12\x34" * 320)
        if matching:
            await asyncio.wait_for(ws.connect(), 2)
            on_start.assert_awaited_once()
            assert len(observed) == 1
            assert observed[0]["event"] == "media"
        else:
            with pytest.raises(ValueError, match="does not match"):
                await asyncio.wait_for(ws.connect(), 2)
            on_start.assert_not_awaited()
            assert not observed
    finally:
        await ws.close()
        await runner.cleanup()
