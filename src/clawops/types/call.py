from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from .._models import BaseModel


class Call(BaseModel):
    """통화 정보를 나타내는 모델.

    아웃바운드 전화 발신 후 반환되거나, 통화 목록/단건 조회 시 반환됩니다.

    Attributes:
        call_id: 통화 고유 식별자 (예: 'CAabcdef1234567890').
        status: 통화 상태. 진행 중은 queued / ringing / in-progress, 종료 상태는
            completed(응답 후 정상 종료) / no-answer(벨은 울렸으나 무응답) /
            busy(통화중) / rejected(수신 거절) / canceled(응답 전 발신 측 취소) /
            failed(시스템·망 오류). completed 만이 실제로 연결된 통화를 의미한다.
        to: 수신 전화번호 또는 SIP URI.
        from_: 발신 전화번호 (계정에 등록된 번호).
        direction: 통화 방향. outbound 또는 inbound.
        duration: 통화 시간 (초). 통화 중이거나 미연결인 경우 None.
        account_id: 계정 ID.
        date_created: 통화 생성 시각.
        date_updated: 통화 종료 시각. 종료 전이면 None.
        recording_url: 녹음 다운로드 경로(상대). 녹음이 없으면 None.
            예: '/v1/accounts/AC.../recordings/CA...'. 다운로드는
            ``client.accounts(account_id).recordings.download(call_id)`` 사용.
        answered_by: AMD(machine_detection) 결과. machine_detection 을 켠 발신
            통화에만 값이 있으며 human(사람) / machine(자동응답기·음성사서함) /
            unknown(판정 불가). 미사용 시 None.
    """

    call_id: str
    status: Literal[
        "queued",
        "ringing",
        "in-progress",
        "completed",
        "failed",
        "busy",
        "no-answer",
        "canceled",
        "rejected",
    ]
    to: str
    from_: str
    direction: Literal["outbound", "inbound"]
    duration: Optional[int] = None
    recording_url: Optional[str] = None
    answered_by: Optional[Literal["human", "machine", "unknown"]] = None
    account_id: str
    date_created: datetime
    date_updated: Optional[datetime] = None


class CallControlResponse(BaseModel):
    """통화 제어 (종료) 응답.

    Attributes:
        call_id: 제어된 통화의 ID.
        status: 변경된 상태 (현재 'completed'만 지원).
    """

    call_id: str
    status: str
