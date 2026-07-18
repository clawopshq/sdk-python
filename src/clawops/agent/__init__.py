"""ClawOps Agent - AI 음성 에이전트 프레임워크."""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from ._agent import ClawOpsAgent, ToolConfig
from ._builtin_tools import BuiltinTool
from ._media_ws import MediaWebSocket
from ._session import CallSession
from ._tool import ToolRegistry, function_tool
from .pipeline import Session

if TYPE_CHECKING:
    from .pipeline import OpenAIRealtime, GeminiRealtime

__all__ = [
    "BuiltinTool",
    "CallSession",
    "ClawOpsAgent",
    "MediaWebSocket",
    "ToolConfig",
    "ToolRegistry",
    "function_tool",
    "Session",
    "OpenAIRealtime",
    "GeminiRealtime",
]

_LAZY = {
    "OpenAIRealtime": ".pipeline",
    "GeminiRealtime": ".pipeline",
}


def __getattr__(name: str):
    if name in _LAZY:
        mod = importlib.import_module(_LAZY[name], __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
