from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional, Union

from .._models import BaseModel

# ⚠️ 응답 어휘는 서버(그리고 그 너머의 카카오)가 소유한다. 닫힌 Literal 로 두면
# 값이 하나 늘어날 때마다 pydantic 이 던지고, 그 값이 섞인 **목록 전체**가 조회
# 불능이 된다. Union[Literal[...], str] 은 타입체커에겐 str 이지만 IDE 자동완성은
# 살아 있다. types/message.py 와 같은 판단이다.
KakaoChannelStatus = Union[Literal["connected", "needs_attention"], str]
"""채널 연결 상태.

``connected`` = 연결 완료. ``needs_attention`` = 연결 기록은 있으나 카카오 채널
상태를 확인하지 못한 상태(실제로 끊겼을 수도, 일시적인 조회 실패일 수도 있다).
상세 조회를 다시 호출하면 재확인한다.
"""

KakaoChannelListStatus = Union[Literal["connected", "needs_attention", "all"], str]
"""채널 목록 필터. 미지정 시 전체(``all``)."""

BrandBubbleType = Union[
    Literal[
        "TEXT",
        "IMAGE",
        "WIDE",
        "WIDE_ITEM_LIST",
        "CAROUSEL_FEED",
        "COMMERCE",
        "CAROUSEL_COMMERCE",
    ],
    str,
]
"""브랜드 메시지 말풍선 유형. **이 값이 단가를 정한다.**

위와 같은 이유로 열어 둔다. ``PREMIUM_VIDEO`` 는 카카오TV 종료로 등록 경로가 막혀
알려진 값에서 뺐다.
"""


class KakaoChannel(BaseModel):
    """이 계정에 연결된 카카오 비즈니스 채널.

    Attributes:
        id: **ClawOps 채널 리소스 ID.** 템플릿 조회와 발송 요청에 쓰는 값이다.
        search_id: 카카오 채널 검색용 ID (``@`` 없는 형태).
            ⚠️ **채널 소유자가 카카오 비즈니스에서 바꿀 수 있으므로 연동 키로 쓰지 말 것** —
            키는 ``id`` 다.
        name: 카카오 채널 이름.
        category_code: 채널 업종 카테고리 코드.
        status: 연결 상태. :data:`KakaoChannelStatus` 참고.
        manager_phone_masked: 담당자 휴대전화번호(마스킹). 원문은 저장하지 않는다.
        connected_at: 채널이 이 계정에 연결된 시각.
        synced_at: 카카오 채널 상태를 마지막으로 확인한 시각.
            ``None`` 이면 연결 이후 한 번도 확인하지 않은 상태다.
        created_at: 생성 시각.
        updated_at: 수정 시각.
    """

    id: str
    search_id: str
    name: str
    category_code: str
    status: KakaoChannelStatus
    manager_phone_masked: Optional[str] = None
    connected_at: datetime
    synced_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class KakaoTemplate(BaseModel):
    """알림톡 템플릿.

    Attributes:
        id: **ClawOps 템플릿 리소스 ID.** 발송의 ``kakao["template_id"]`` 에 쓴다.
        channel_id: ClawOps 채널 리소스 ID. 발송의 ``kakao["channel_id"]`` 와 같은 값이다.
        name: 템플릿 이름.
        content: 카카오 검수를 받은 템플릿 본문.
        status: 카카오 검수 상태 (예 ``"APPROVED"``).
            ⚠️ **발송 가능 판정의 정본은 이 값이 아니라** ``sendable`` 이다. 검수 상태와
            휴면 여부를 서버가 합쳐 계산한 결과가 ``sendable`` 이고, 카카오 쪽 어휘는
            늘어날 수 있어 문자열로 둔다.
        dormant: 휴면 여부. ``True`` 면 승인 상태여도 발송할 수 없다.
        sendable: 지금 발송에 쓸 수 있으면 ``True``.
        assign_type: ``"CHANNEL"``(채널 소유) 또는 ``"GROUP"``(기본 제공).
        message_type: 카카오 메시지 유형 코드 (예 ``"BA"``).
        emphasize_type: 강조 유형 코드 (예 ``"NONE"``).
        variables: 발송 시 ``kakao["variables"]`` 에 **모두** 채워야 하는 변수 이름.
            버튼 링크나 강조 문구에 들어간 변수도 이 목록에 포함된다.
        created_at: 생성 시각.
        updated_at: 수정 시각.
    """

    id: str
    channel_id: str
    name: str
    content: str
    status: str
    dormant: bool
    sendable: bool
    assign_type: str
    message_type: str
    emphasize_type: str
    variables: list[str]
    created_at: datetime
    updated_at: datetime


class KakaoBrandTemplate(BaseModel):
    """브랜드 메시지 템플릿.

    ⭐ **알림톡과 달리 검수가 없다** — ``status``·``dormant``·``sendable`` 이 없는
    이유이고, 목록에 있으면 곧 발송할 수 있다.

    Attributes:
        id: **ClawOps 템플릿 리소스 ID.** 발송의 ``brand["template_id"]`` 에 쓴다.
        channel_id: ClawOps 채널 리소스 ID. 발송의 ``brand["channel_id"]`` 와 같은 값이다.
        name: 템플릿 이름.
        chat_bubble_type: 말풍선 유형. 텍스트형이 가장 싸고 와이드리스트·캐러셀·
            커머스가 가장 비싸다. 근거는 :data:`BrandBubbleType` 참고.
        content: 말풍선 본문. ⚠️ **유형에 따라 ``None`` 이다** — 본문이 담기는 자리가
            유형마다 달라 ``TEXT``·``IMAGE``·``WIDE`` 에만 채워진다.
        header: 와이드리스트형의 머리말. 다른 유형에서는 ``None``.
        variables: 발송 시 ``brand["variables"]`` 에 모두 채워야 하는 변수 이름.
        created_at: 생성 시각.
        updated_at: 수정 시각.
    """

    id: str
    channel_id: str
    name: str
    chat_bubble_type: BrandBubbleType
    content: Optional[str] = None
    header: Optional[str] = None
    variables: list[str]
    created_at: datetime
    updated_at: datetime


class KakaoChannelCategory(BaseModel):
    """채널 업종 카테고리.

    Attributes:
        code: 채널 연결 시 ``category_code`` 로 그대로 보내는 값.
        name: 사람이 읽는 카테고리 이름.
    """

    code: str
    name: str


class KakaoChannelCategoryMeta(BaseModel):
    """카테고리 목록의 메타데이터.

    Attributes:
        fetched_at: 이 목록을 공급자에서 실제로 받아 온 시각.
        cached: 서버 캐시에서 응답했는지 여부. 캐시는 짧고, 값이 바뀌면 자동으로 따라간다.
    """

    fetched_at: datetime
    cached: bool


class KakaoChannelCategoryList(BaseModel):
    """채널 카테고리 조회 응답.

    ⚠️ **열린 집합이다.** 값은 카카오/공급자 쪽에서 늘거나 바뀌므로 코드에 하드코딩하지
    말고 이 응답을 그대로 선택지로 쓴다. 페이지네이션이 없어 Page 가 아니다.

    Attributes:
        data: 선택 가능한 카테고리.
        meta: 조회 시각과 캐시 여부.
    """

    data: list[KakaoChannelCategory]
    meta: KakaoChannelCategoryMeta


class KakaoTokenRequest(BaseModel):
    """인증번호 발송 요청 접수 결과 (202).

    ⚠️ **응답에 인증번호는 없습니다.** 인증번호는 카카오 비즈니스 채널에 등록된 담당자
    휴대전화로만 전달되고 ClawOps 는 그 값을 받지도 저장하지도 않습니다. 그래서 성공이
    200 이 아니라 202 입니다.

    Attributes:
        requested: 요청이 접수되었는지 여부.
        search_id: 정규화된 검색용 ID. 다음 단계(``connect``)에 이 값을 그대로 보낸다.
        phone_number_masked: 인증번호가 발송된 번호(마스킹).
        retry_after_seconds: 재요청까지 기다릴 시간(초).
    """

    requested: bool
    search_id: str
    phone_number_masked: str
    retry_after_seconds: int
