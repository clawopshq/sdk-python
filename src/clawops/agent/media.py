"""Explicit per-session audio contracts. Select before prewarm; never infer from bytes.

PCM is signed 16-bit little-endian mono on Media WebSocket. This is not RTP
L16 (network byte order). The server must negotiate support before selecting it.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any


class MediaProfile(str, Enum):
    MULAW_8000 = "mulaw_8000"
    PCM_S16LE_16000 = "pcm_s16le_16000"

    @property
    def sample_rate(self) -> int:
        return 8000 if self is MediaProfile.MULAW_8000 else 16000

    @property
    def bytes_per_sample(self) -> int:
        return 1 if self is MediaProfile.MULAW_8000 else 2

    @property
    def bytes_per_second(self) -> int:
        return self.sample_rate * self.bytes_per_sample

    @property
    def frame_bytes(self) -> int:
        return self.bytes_per_second // 50  # 20 ms

    @property
    def silence_byte(self) -> bytes:
        return b"\xff" if self is MediaProfile.MULAW_8000 else b"\x00"

    @property
    def encoding(self) -> str:
        return (
            "audio/x-mulaw"
            if self is MediaProfile.MULAW_8000
            else "audio/pcm;encoding=signed-integer;bits=16;endianness=little"
        )

    def validate_start(self, info: Mapping[str, Any]) -> None:
        """Fail before delivering media if the server selected a different contract."""
        if (info.get("encoding", "audio/x-mulaw"), info.get("sample_rate", 8000), info.get("channels", 1)) != (
            self.encoding,
            self.sample_rate,
            1,
        ):
            raise ValueError(f"Media format does not match selected profile {self.value}")
