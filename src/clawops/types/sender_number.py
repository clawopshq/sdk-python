from __future__ import annotations

from datetime import datetime
from typing import Optional

from .._models import BaseModel


class SenderNumber(BaseModel):
    """문자 발신번호(clawops#1489). 문자는 등록해 승인된 발신번호로만 보낼 수 있습니다.

    Attributes:
        id: 발신번호 id. 삭제할 때 씁니다.
        number: 발신번호(숫자만).
        type: '070' | 'representative'(대표번호) | 'mobile'(이동전화).
        verification_method: 'own_provisioned'(ClawOps 개통) | 'identity_verified'(본인확인 이동전화)
            | 'usage_cert_ars'(다른 통신사 대표번호).
        status: 'approved' = 발송에 쓸 수 있음, 'pending' = 승인 대기.
        created_at: 등록 시각.
    """

    id: str
    number: str
    # ⛔ Literal 로 좁히지 않는다 — 서버가 값을 늘리면 옛 SDK 의 목록 조회가 통째로 깨진다.
    type: str
    verification_method: str
    status: str
    created_at: datetime


class SenderNumberGrace(BaseModel):
    """등록하지 않았지만 유예 기간이라 ``expires_at`` 전까지 보낼 수 있는 번호."""

    number: str
    expires_at: datetime


class SenderNumberLimit(BaseModel):
    """070번호·대표번호 회선 수 한도. 계정이 아니라 명의 기준이라 같은 명의의 다른 계정 등록분도 셉니다.

    Attributes:
        used: 이 명의로 등록한 회선 수(승인 대기 포함).
        limit: 등록할 수 있는 회선 수. None 이면 아직 정해지지 않았습니다(법인 종사자 수 증빙 전).
        reached: True 면 더 등록할 수 없습니다.
    """

    used: int
    limit: Optional[int] = None
    reached: bool


class SenderNumberList(BaseModel):
    """``sender_numbers.list()`` 응답.

    Attributes:
        sender_numbers: 등록된 발신번호(승인·승인 대기), 최근 등록 순.
        grace: 유예 기간인 번호.
        limit: 회선 수 한도. 명의를 확인할 수 없으면 None.
    """

    sender_numbers: list[SenderNumber]
    grace: list[SenderNumberGrace]
    limit: Optional[SenderNumberLimit] = None


class SmsSenderRegistration(BaseModel):
    """번호 발급 응답의 문자 발신번호 자동 등록 결과.

    ``registered`` 가 False 여도 번호 발급은 성공입니다 — 그 번호로는 아직 문자를 보낼 수 없고,
    ``reason`` 을 해결한 뒤 ``sender_numbers.create()`` 로 등록합니다.

    Attributes:
        registered: 등록됐는지.
        sender_number_id: registered 가 True 일 때 발신번호 id.
        reason: registered 가 False 일 때 이유 — 'sender_number_limit_reached'(한도 도달),
            'sender_number_limit_unknown'(한도 미정), 'sender_number_holder_unresolved'(명의 확인 불가),
            'actor_unknown', 'unexpected'(일시 오류).
    """

    registered: bool
    sender_number_id: Optional[str] = None
    reason: Optional[str] = None
