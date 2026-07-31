from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from .._models import BaseModel

BlockedChannel = Literal["call", "message"]
BlockedRecipientStatus = Literal["active", "released", "all"]
BlockedRecipientSource = Literal["api", "console", "import"]


class BlockedRecipient(BaseModel):
    """수신거부(DNC) 항목.

    등록된 번호는 해당 계정의 **발신**(전화·문자)에서 제외됩니다. 착신은 막지 않습니다.
    같은 번호라도 채널(call/message)마다 별개 항목입니다.

    Attributes:
        id: 항목 ID.
        number: 국내 표기로 정규화된 번호 (예 '01012345678').
        channel: 'call'(전화) 또는 'message'(문자 — SMS/LMS/MMS 공통).
        active: 지금 차단 중인지 여부. 해제된 항목도 이력으로 남아 조회되므로 이 값으로 구분합니다.
        source: 접수 경로. 공개 API 등록은 api/console/import, 내부 접수는 ars/sms/agent.
        source_ref: 증빙 링크(통화 id 또는 메시지 id). 가리키는 대상은 source 가 결정합니다.
        note: 자유 메모.
        created_by: 등록 주체. 자동 접수는 None.
        created_at: 수신거부 접수 시각.
        updated_at: 마지막 변경 시각.
        unblocked_at: 해제 시각. None 이면 차단 중.
        unblocked_source: 해제 경로.
        unblocked_by: 해제 주체. 자동 해제는 None.
        unblocked_note: 해제 사유 메모.
    """

    id: str
    number: str
    channel: BlockedChannel
    active: bool
    source: str
    source_ref: Optional[str] = None
    note: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    unblocked_at: Optional[datetime] = None
    unblocked_source: Optional[str] = None
    unblocked_by: Optional[str] = None
    unblocked_note: Optional[str] = None
