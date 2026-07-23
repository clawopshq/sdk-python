from __future__ import annotations

from typing import Annotated, Literal, Union

from typing_extensions import Required, TypedDict

from .._utils import PropertyInfo

# ── Call API 파라미터 ────────────────────────────────────────────────────────


class CallCreateParams(TypedDict, total=False):
    """발신 전화 생성 요청 파라미터.

    **2가지 모드:**

    - **VoiceML 모드**: ``url``\\을 지정하면 VoiceML로 통화를 제어합니다.
    - **Agent 모드**: ``url``\\을 생략하면 Agent SDK로 통화가 연결됩니다.
    """

    to: Required[Annotated[str, PropertyInfo(alias="To")]]
    """수신 대상. 전화번호(PSTN) 또는 sip: URI(내선)."""

    from_: Required[Annotated[str, PropertyInfo(alias="From")]]
    """발신 번호. 계정에 등록된 번호여야 합니다."""

    url: Annotated[str, PropertyInfo(alias="Url")]
    """통화 연결 시 VoiceML 명령을 반환할 URL."""

    status_callback: Annotated[str, PropertyInfo(alias="StatusCallback")]
    """통화 상태 변경 시 POST 요청을 받을 콜백 URL."""

    status_callback_event: Annotated[str, PropertyInfo(alias="StatusCallbackEvent")]
    """수신할 상태 이벤트 목록 (공백 구분)."""

    timeout: Annotated[int, PropertyInfo(alias="Timeout")]
    """발신 타임아웃 (초). 기본값: 60."""


class CallListParams(TypedDict, total=False):
    """통화 목록 조회 요청 파라미터."""

    status: Literal["queued", "ringing", "in-progress", "completed", "failed"]
    """통화 상태로 필터링."""

    from_: Union[str, list[str]]
    """발신번호로 필터링. 리스트 시 IN 조건."""

    to: Union[str, list[str]]
    """수신번호로 필터링. 리스트 시 IN 조건."""

    number: Union[str, list[str]]
    """관여 번호로 필터링 (from OR to 매칭). 리스트 시 IN 조건."""

    page: int
    """페이지 번호 (0부터 시작)."""

    page_size: Annotated[int, PropertyInfo(alias="pageSize")]
    """페이지당 항목 수 (기본 20, 최대 100)."""


class CallUpdateParams(TypedDict, total=False):
    """통화 제어 (종료) 요청 파라미터."""

    status: Required[Annotated[Literal["completed"], PropertyInfo(alias="Status")]]
    """변경할 통화 상태. 현재 'completed'만 지원."""
