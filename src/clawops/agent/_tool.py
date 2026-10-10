"""function_tool 데코레이터와 ToolRegistry.

OpenAI Realtime API의 tool 스키마를 자동 생성하고,
등록된 핸들러를 이름으로 호출한다.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Callable

from .._exceptions import AgentError

log = logging.getLogger("clawops.agent")

TOOL_BEHAVIORS = ("BLOCKING", "NON_BLOCKING")
"""도구 선언의 ``behavior``(Gemini Live). BLOCKING=결과를 받을 때까지 모델이 기다림, NON_BLOCKING=도구가
도는 동안 모델이 계속 대화."""

TOOL_SCHEDULINGS = ("SILENT", "WHEN_IDLE", "INTERRUPT")
"""NON_BLOCKING 도구 결과를 모델이 어떻게 다룰지(Gemini Live ``FunctionResponse.scheduling``).
SILENT=말하지 않고 기억만, WHEN_IDLE=지금 말을 마친 뒤 전달(서버 기본값), INTERRUPT=즉시 끼어들어 전달."""


def _validate_tool_options(behavior: str | None, scheduling: str | None) -> None:
    if behavior is not None and behavior not in TOOL_BEHAVIORS:
        raise ValueError(f"behavior must be one of {TOOL_BEHAVIORS}, got {behavior!r}")
    if scheduling is not None and scheduling not in TOOL_SCHEDULINGS:
        raise ValueError(f"scheduling must be one of {TOOL_SCHEDULINGS}, got {scheduling!r}")


_PY_TYPE_TO_JSON: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _param_annotations(fn: Callable[..., Any]) -> dict[str, Any]:
    """return 을 뺀 파라미터 어노테이션.

    inspect.get_annotations 는 3.10+ 라 쓰지 않는다 — requires-python 은 >=3.9 다
    (clawops#1252). 함수·바운드 메서드에서는 __annotations__ 와 같은 값이다.
    """
    ann = getattr(fn, "__annotations__", None) or {}
    return {k: v for k, v in ann.items() if k != "return"}


@dataclass
class FunctionTool:
    name: str
    description: str
    parameters: dict[str, Any]
    required: list[str]
    handler: Callable[..., Awaitable[str]]
    behavior: str | None = None
    """Gemini Live 함수 선언 ``behavior``. None 이면 세션 기본값(모델별)을 따른다."""
    scheduling: str | None = None
    """NON_BLOCKING 결과의 ``scheduling``. None 이면 세션 기본값, 그것도 없으면 서버 기본값(WHEN_IDLE)."""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, FunctionTool] = {}
        self._mcp_tools: dict[str, tuple[Any, dict[str, Any]]] = {}

    def register(
        self,
        fn: Callable[..., Awaitable[str]] | None = None,
        *,
        behavior: str | None = None,
        scheduling: str | None = None,
    ) -> Any:
        """도구를 등록한다. ``@registry.register`` 와 ``@registry.register(behavior=...)`` 둘 다 된다.

        ``behavior``·``scheduling`` 은 Gemini Live 에서만 쓰이고 다른 세션은 무시한다.
        """
        _validate_tool_options(behavior, scheduling)
        if fn is None:

            def decorator(f: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
                return self.register(f, behavior=behavior, scheduling=scheduling)

            return decorator

        sig = inspect.signature(fn)
        hints = _param_annotations(fn)

        properties: dict[str, Any] = {}
        required: list[str] = []

        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            py_type = hints.get(param_name, str)
            json_type = _PY_TYPE_TO_JSON.get(py_type, "string")
            properties[param_name] = {"type": json_type}
            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        tool = FunctionTool(
            name=fn.__name__,
            description=(fn.__doc__ or "").strip(),
            parameters=properties,
            required=required,
            handler=fn,
            behavior=behavior,
            scheduling=scheduling,
        )
        self._tools[fn.__name__] = tool
        log.debug("Tool registered: %s (params: %s)", tool.name, list(properties.keys()))
        return fn

    def register_mcp_tools(self, clients: list[Any]) -> None:
        for client in clients:
            for schema in client.tools:
                name = schema["name"]
                if name in self._tools or name in self._mcp_tools:
                    raise AgentError(f"Tool name conflict: {name}")
                self._mcp_tools[name] = (client, schema)
                log.debug("MCP tool registered: %s", name)
        log.debug("Total tools: %d local + %d MCP", len(self._tools), len(self._mcp_tools))

    def clear_mcp_tools(self) -> None:
        if self._mcp_tools:
            log.debug("Clearing %d MCP tools", len(self._mcp_tools))
        self._mcp_tools.clear()

    def fork(self) -> ToolRegistry:
        """로컬 tool을 복사한 새 ToolRegistry를 반환 (MCP tool은 비어있음)."""
        copy = ToolRegistry()
        copy._tools = dict(self._tools)
        return copy

    def __contains__(self, name: str) -> bool:
        return name in self._tools or name in self._mcp_tools

    def __getitem__(self, name: str) -> FunctionTool:
        return self._tools[name]

    def to_openai_tools(self) -> list[dict[str, Any]]:
        result = []
        for tool in self._tools.values():
            result.append(
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": tool.parameters,
                        "required": tool.required,
                    },
                }
            )
        for _client, schema in self._mcp_tools.values():
            result.append(schema)
        return result

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        if name in self._mcp_tools:
            client, _schema = self._mcp_tools[name]
            log.debug("Calling MCP tool: %s(%s)", name, arguments)
            return await client.call_tool(name, arguments)
        if name not in self._tools:
            raise KeyError(f"Tool not found: {name}")
        tool = self._tools[name]
        hints = _param_annotations(tool.handler)
        converted = {}
        for k, v in arguments.items():
            target_type = hints.get(k, str)
            try:
                converted[k] = target_type(v)
            except (ValueError, TypeError):
                converted[k] = v
        return await tool.handler(**converted)


def function_tool(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
    """Standalone decorator (ClawOpsAgent 없이 사용 시). 실제로는 @agent.tool 권장."""
    return fn
