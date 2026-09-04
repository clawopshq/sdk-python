from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional, Union

from .._models import BaseModel

# ⚠️ 닫힌 Literal 로 두지 않는다. 응답 스키마의 어휘는 **서버가 소유**하므로
# 서버가 값을 하나 늘리는 순간 pydantic 이 던지고, 그러면 그 값이 섞인 목록
# **전체**가 조회 불능이 된다. 실제로 그렇게 깨져 있었다 — 서버는 알림톡을
# `ata` 로 주는데 SDK 는 `kakao` 를 기다리고 있어서, 콘솔로 알림톡을 한 번이라도
# 보낸 계정은 messages.get() 도 messages.list() 도 예외를 받았다.
#
# Union[Literal[...], str] 은 타입체커에겐 str 이지만 IDE 자동완성은 살아 있다.
# 넓은 파서 쪽이 안전하다 — 모르는 값은 그대로 통과시킨다.
MessageType = Union[Literal["sms", "lms", "mms", "ata", "bms"], str]
"""메시지 유형. `ata` 는 카카오 알림톡, `bms` 는 카카오 브랜드 메시지. 서버가 값을 늘릴 수 있어 열려 있다."""

MessageStatus = Union[Literal["queued", "sending", "sent", "failed", "received"], str]
"""메시지 상태. 서버가 값을 늘릴 수 있어 열려 있다."""


class Message(BaseModel):
    """메시지 정보를 나타내는 모델.

    메시지 발송 후 반환되거나, 메시지 목록/단건 조회 시 반환됩니다.

    Attributes:
        message_id: 메시지 고유 식별자 (예: 'MG0123456789abcdef...').
        status: 메시지 상태.
        type: 메시지 유형. sms, lms, mms, ata 중 하나.
            ``ata`` 는 **카카오 알림톡**입니다. 이때 ``body`` 는 템플릿에 변수를
            치환한 결과이며, 버튼·아이템 리스트·강조 문구는 템플릿에 검수된 대로
            발송되어 이 값에는 담기지 않습니다.
        subject: 메시지 제목 (LMS/MMS 등에서 사용).
        to: 수신 번호.
        from_: 발신 번호.
        body: 메시지 본문.
        direction: 메시지 방향.
        account_id: 계정 ID.
        date_created: 생성 시각.
        date_updated: 수정 시각.
    """

    message_id: str
    status: MessageStatus
    type: MessageType
    to: str
    from_: str
    subject: Optional[str] = None
    body: Optional[str] = None
    num_media: int = 0
    media_url: Optional[list[str]] = None
    direction: Literal["outbound", "inbound"]
    account_id: str
    date_created: datetime
    date_updated: Optional[datetime] = None
