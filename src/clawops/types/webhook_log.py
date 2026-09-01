from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from .._models import BaseModel


class WebhookLog(BaseModel):
    """Webhook 발송 로그 모델.

    Attributes:
        id: 로그 고유 식별자.
        webhook_id: Webhook ID.
        event: 이벤트 유형 (예: 'message.sent').
        request_url: 요청 URL.
        request_payload: 요청 페이로드.
        response_status: HTTP 응답 상태 코드.
        response_body: 응답 본문.
        response_time_ms: 응답 시간 (밀리초).
        status: 발송 상태. pending, delivered, failed 중 하나.
        attempt: 현재 시도 횟수.
        max_attempts: 최대 재시도 횟수.
        created_at: 생성 시각.
        completed_at: 완료 시각.
    """

    id: str
    webhook_id: str
    event: str
    request_url: str
    request_payload: dict[str, Any]
    response_status: Optional[int] = None
    response_body: Optional[str] = None
    response_time_ms: Optional[int] = None
    status: Literal["pending", "delivered", "failed"]
    attempt: int
    max_attempts: int
    created_at: datetime
    completed_at: Optional[datetime] = None
