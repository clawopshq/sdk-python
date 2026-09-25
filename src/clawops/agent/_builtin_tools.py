"""Built-in tool 정의 및 선택 상수."""

from __future__ import annotations

from enum import Enum


# StrEnum 은 3.11+ 라 쓰지 않는다 — requires-python 은 >=3.9 다(clawops#1252).
class BuiltinTool(str, Enum):
    """Agent가 기본 제공하는 내장 도구.

    개별 도구를 리스트로 전달하거나, ALL / NONE 상수를 사용할 수 있습니다.
    """

    HANG_UP = "hang_up"
    COLLECT_DTMF = "collect_dtmf"
    SEND_DTMF = "send_dtmf"
    TRANSFER_CALL = "transfer_call"

    ALL = "all"
    NONE = "none"

    # StrEnum 과 같게 str() 이 값을 돌려주도록 맞춘다.
    def __str__(self) -> str:
        return str(self.value)


def resolve_builtin_tools(
    value: BuiltinTool | list[BuiltinTool],
) -> set[BuiltinTool]:
    """사용자 입력을 실제 도구 set으로 변환."""
    if isinstance(value, BuiltinTool):
        if value == BuiltinTool.ALL:
            return {BuiltinTool.HANG_UP, BuiltinTool.COLLECT_DTMF, BuiltinTool.SEND_DTMF, BuiltinTool.TRANSFER_CALL}
        if value == BuiltinTool.NONE:
            return set()
        return {value}
    return set(value) - {BuiltinTool.ALL, BuiltinTool.NONE}
