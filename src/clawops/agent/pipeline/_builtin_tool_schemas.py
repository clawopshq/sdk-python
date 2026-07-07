"""Built-in tool 스키마 정의, 포맷 변환, 실행 헬퍼.

모든 세션(PipelineSession, OpenAIRealtime, GeminiRealtime)이 공통으로 사용하는
내장 도구 스키마를 한 곳에서 관리한다.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from .._builtin_tools import BuiltinTool
from .._session import CallSession

# ── 정규 스키마 (neutral 포맷) ──────────────────────────────────────

_HANG_UP = {
    "name": "hang_up",
    "description": "End the phone call. Use when the conversation is finished or the caller says goodbye.",
    "parameters": {"type": "object", "properties": {}},
}

_COLLECT_DTMF = {
    "name": "collect_dtmf",
    "description": "사용자로부터 DTMF(전화 키패드) 입력을 수집합니다. 반드시 사용자에게 무엇을 입력해야 하는지 안내한 후 호출하세요.",
    "parameters": {
        "type": "object",
        "properties": {
            "max_digits": {"type": "integer", "description": "수집할 최대 자릿수"},
            "finish_on_key": {"type": "string", "description": "입력 종료 키 (기본: #)"},
            "timeout": {"type": "integer", "description": "입력 대기 시간(초, 기본: 5)"},
        },
        "required": ["max_digits"],
    },
}

_SEND_DTMF = {
    "name": "send_dtmf",
    "description": "DTMF 신호를 전송합니다. ARS 메뉴 탐색이나 내선번호 입력 시 사용합니다.",
    "parameters": {
        "type": "object",
        "properties": {
            "digits": {
                "type": "string",
                "description": "전송할 번호 (0-9, *, #). 'w'는 500ms 대기, 'W'는 1000ms 대기. 예: '1', '1234#', '1w2'",
            },
        },
        "required": ["digits"],
    },
}

_TRANSFER_CALL = {
    "name": "transfer_call",
    "description": "Transfer the current call to another phone number or SIP endpoint. Use for blind transfer (direct handoff) or warm transfer (with whisper message to the target).",
    "parameters": {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Transfer destination. A phone number when destination_type is 'pstn', or a SIP URI (e.g. 'sip:user@host') when destination_type is 'sip'.",
            },
            "destination_type": {
                "type": "string",
                "enum": ["pstn", "sip"],
                "description": "pstn: dial a phone number via carrier (default). sip: connect directly to a SIP endpoint (no carrier/PSTN).",
            },
            "mode": {
                "type": "string",
                "enum": ["blind", "warm"],
                "description": "blind: direct transfer (default), warm: play whisper to target first",
            },
            "after_transfer": {
                "type": "string",
                "enum": ["terminate", "return"],
                "description": "terminate: end AI session (default), return: AI resumes after transfer ends",
            },
            "whisper": {
                "type": "string",
                "description": "Message to speak to transfer target before connecting customer (warm mode only)",
            },
            "caller_id": {
                "type": "string",
                "description": "Override caller ID for the transfer leg",
            },
            "timeout": {
                "type": "integer",
                "description": "Seconds to wait for transfer target to answer (default 30)",
            },
        },
        "required": ["to"],
    },
}

_TOOL_MAP: dict[BuiltinTool, dict[str, Any]] = {
    BuiltinTool.HANG_UP: _HANG_UP,
    BuiltinTool.COLLECT_DTMF: _COLLECT_DTMF,
    BuiltinTool.SEND_DTMF: _SEND_DTMF,
    BuiltinTool.TRANSFER_CALL: _TRANSFER_CALL,
}

BUILTIN_TOOL_NAMES = frozenset(s["name"] for s in _TOOL_MAP.values())


# ── 포맷 변환 ───────────────────────────────────────────────────────

def _to_chat_completions(schema: dict[str, Any]) -> dict[str, Any]:
    """Chat Completions 포맷: ``{"type":"function","function":{...}}``."""
    return {
        "type": "function",
        "function": {
            "name": schema["name"],
            "description": schema["description"],
            "parameters": schema["parameters"],
        },
    }


def _to_realtime(schema: dict[str, Any]) -> dict[str, Any]:
    """OpenAI Realtime 포맷: ``{"type":"function","name":...}``."""
    return {
        "type": "function",
        "name": schema["name"],
        "description": schema["description"],
        "parameters": schema["parameters"],
    }


def _to_gemini(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini 포맷: ``{"name":...}`` (type 없음)."""
    return {
        "name": schema["name"],
        "description": schema["description"],
        "parameters": schema["parameters"],
    }


_CONVERTERS = {
    "chat": _to_chat_completions,
    "realtime": _to_realtime,
    "gemini": _to_gemini,
}


def get_builtin_tool_schemas(
    builtin_tools: set[BuiltinTool] | None,
    fmt: Literal["chat", "realtime", "gemini"],
) -> list[dict[str, Any]]:
    """활성화된 builtin tool 스키마를 요청한 포맷으로 반환."""
    converter = _CONVERTERS[fmt]
    result: list[dict[str, Any]] = []
    for tool_enum, schema in _TOOL_MAP.items():
        if builtin_tools is None or tool_enum in builtin_tools:
            result.append(converter(schema))
    return result


# ── 공통 실행 헬퍼 ──────────────────────────────────────────────────

async def execute_builtin_tool(
    func_name: str,
    args: dict[str, Any],
    call: CallSession,
) -> str | None:
    """Builtin tool을 실행하고 결과 문자열을 반환한다.

    ``func_name`` 이 builtin tool이 아니면 ``None`` 을 반환한다.
    ``hang_up`` 의 경우 빈 문자열 ``""`` 을 반환한다 (호출자가 종료 처리).
    """
    if func_name == "hang_up":
        await call.hangup()
        return ""
    if func_name == "collect_dtmf":
        try:
            result = await call.collect_dtmf(
                max_digits=args.get("max_digits", 4),
                finish_on_key=args.get("finish_on_key", "#"),
                timeout=args.get("timeout", 5),
            )
            return result if result else "(타임아웃 - 입력 없음)"
        except Exception as e:
            return f"Error: {e}"
    if func_name == "send_dtmf":
        try:
            await call.send_dtmf_sequence(args.get("digits", ""))
            return "sent"
        except Exception as e:
            return f"Error: {e}"
    if func_name == "transfer_call":
        try:
            # Fire-and-forget: transfer 요청만 보내고 즉시 반환.
            # call-engine이 transfer 시작 시 media WS를 닫으므로,
            # 결과를 await하면 LLM 세션이 먼저 종료된다.
            import asyncio
            asyncio.ensure_future(call.transfer(**args))
            return json.dumps({"status": "transfer_initiated"})
        except Exception as e:
            return f"Error: {e}"
    return None
