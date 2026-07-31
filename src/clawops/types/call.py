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
        hangup_cause: 통화 종료 사유. status 가 왜 그렇게 끝났는지를 구분한다 —
            특히 failed 는 결번·망 오류·시스템 오류를 모두 포함하는 대분류라,
            발신 리스트를 정제하려면 status 가 아니라 이 값을 봐야 한다.
            재시도해도 소용없음: invalid_number(결번) / number_changed /
            incompatible_destination. 재시도 가치 있음: no_answer / user_busy /
            temporary_failure / switching_congestion / no_circuit_available /
            network_out_of_order / destination_out_of_order /
            recovery_on_timer_expire / resource_unavailable. 그 외:
            normal_clearing(정상 종료) / caller_canceled / call_rejected /
            protocol_error / unspecified / app_error·call_stuck(ClawOps 측 오류
            — 재시도 권장) / unknown. 종료 전이거나 사유 미상이면 None.
        hangup_cause_q850: 통신망 Q.850 cause code. 1·5·28=결번, 16=정상해제,
            17=통화중, 18/19/20=무응답, 21=거절, 38=망장애. 사유 미상이면 None.
        sip_response_code: 종료를 유발한 SIP 응답코드. 404=없는 번호, 486=통화중,
            500=망 오류 등. 응답코드 없이 끝났으면 None. 국내 통신망은 실제 사유를
            500 으로 감싸 보내기도 하므로 hangup_cause 가 더 정확하다.
        hangup_source: 종료 책임 주체. carrier(통신망) / callee(수신자) /
            caller(발신자) / app·system(ClawOps 측 오류 — 수신자 번호를 정제
            대상에 넣지 말고 재시도할 것).
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
    # 종료 사유는 Literal 로 좁히지 않는다 — 통신망이 새 cause 를 보내면 서버가 enum 을
    # 넓히는데, 클라이언트가 그때마다 릴리즈되어야 파싱되는 구조는 안 된다.
    hangup_cause: Optional[str] = None
    hangup_cause_q850: Optional[int] = None
    sip_response_code: Optional[int] = None
    hangup_source: Optional[Literal["carrier", "callee", "caller", "app", "system"]] = None
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
