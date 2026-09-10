"""Call telemetry: SDK info and per-call metrics."""

from __future__ import annotations

import platform
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from clawops._version import __version__

MAX_ERRORS = 20
MAX_ERROR_MESSAGE_LENGTH = 200


# 서버에 "이 SDK 를 어떻게 다뤄도 되는지" 를 알려주는 선언.
#
# `retire`: `agent.retired` 통지를 이해하고, 그걸 받으면 **재연결하지 않는다**. 서버는 이
#   선언이 있는 연결에 한해 진행 중 통화가 없을 때 자리를 즉시 정리한다. 선언이 없는(구)
#   SDK 는 서버가 상한까지 붙잡아 둔다 — 즉시 닫으면 그 SDK 가 도로 붙어 방금 인계받은
#   프로세스를 밀어내기 때문이다(핑퐁).
SDK_CAPABILITIES = ("retire",)


def get_sdk_info() -> dict[str, Any]:
    return {
        "name": "clawops-python",
        "version": __version__,
        "runtime": f"python/{sys.version.split()[0]}",
        "os": f"{sys.platform}/{platform.machine()}",
        "capabilities": list(SDK_CAPABILITIES),
    }


@dataclass
class CallMetrics:
    first_response_ms: int | None = None
    turn_count: int = 0
    tool_call_count: int = 0
    tool_error_count: int = 0
    barge_in_count: int = 0
    end_reason: str | None = None
    errors: list[dict[str, str]] = field(default_factory=list)

    _first_response_sent: bool = field(default=False, repr=False)
    _start_time_ms: float = field(default=0, repr=False)

    def record_first_response(self) -> None:
        if not self._first_response_sent:
            self._first_response_sent = True
            self.first_response_ms = int(time.time() * 1000 - self._start_time_ms)

    def record_turn(self) -> None:
        self.turn_count += 1

    def record_tool_call(self) -> None:
        self.tool_call_count += 1

    def record_tool_error(self, err: Exception) -> None:
        self.tool_error_count += 1
        if len(self.errors) < MAX_ERRORS:
            self.errors.append({
                "type": type(err).__name__,
                "message": str(err)[:MAX_ERROR_MESSAGE_LENGTH],
            })

    def record_barge_in(self) -> None:
        self.barge_in_count += 1

    def record_end_reason(self, reason: str) -> None:
        self.end_reason = reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "firstResponseMs": self.first_response_ms,
            "turnCount": self.turn_count,
            "toolCallCount": self.tool_call_count,
            "toolErrorCount": self.tool_error_count,
            "bargeInCount": self.barge_in_count,
            "endReason": self.end_reason,
            "errors": self.errors,
        }
