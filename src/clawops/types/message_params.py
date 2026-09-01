from __future__ import annotations

from typing import Annotated, Literal, Union

from typing_extensions import Required, TypedDict

from .._utils import PropertyInfo

TextMessageType = Literal["sms", "lms", "mms"]
"""문자 유형. 발송·대체발송·목록 필터가 같은 어휘를 쓴다."""


class KakaoSendParam(TypedDict, total=False):
    """카카오 알림톡 발송 지정.

    이 항목을 실으면 알림톡입니다. 채널과 템플릿은 콘솔에서 연결·승인한 것이어야 하고,
    ID 는 ``client.kakao.channels.list()`` / ``client.kakao.templates.list()`` 로 얻습니다.
    """

    channel_id: Required[Annotated[str, PropertyInfo(alias="ChannelId")]]
    """ClawOps 채널 리소스 ID (채널 목록의 ``id``). 카카오 검색용 ID 가 아닙니다."""

    template_id: Required[Annotated[str, PropertyInfo(alias="TemplateId")]]
    """ClawOps 템플릿 리소스 ID (템플릿 목록의 ``id``)."""

    variables: Annotated[dict[str, str], PropertyInfo(alias="Variables")]
    """템플릿 변수. 키는 ``고객명`` 과 ``#{고객명}`` 을 **모두** 받습니다.

    템플릿이 요구하는 변수가 빠지면 ``400 kakao_variable_missing``, 템플릿에 없는
    변수를 주면 ``400 kakao_variable_unknown`` 입니다. 요구 목록은 템플릿의
    ``variables`` 에 있고, 버튼 링크나 강조 문구에 들어간 변수도 같은 목록에
    포함됩니다.
    """


class KakaoFallbackParam(TypedDict, total=False):
    """알림톡이 발송 실패했을 때 대신 나갈 문자.

    생략하면 **템플릿 본문을 그대로** 문자로 보냅니다. 대체 발송된 문자는 별도의
    메시지 1건으로 기록되며 **문자 단가로 따로 청구**됩니다.
    """

    body: Annotated[str, PropertyInfo(alias="Body")]
    """문자 본문. 생략하면 알림톡 본문(변수 치환 결과)을 씁니다."""

    subject: Annotated[str, PropertyInfo(alias="Subject")]
    """문자 제목 (LMS/MMS)."""

    type: Annotated[TextMessageType, PropertyInfo(alias="Type")]
    """생략하면 본문 길이에 맞춰 자동으로 고릅니다."""

    disabled: Annotated[bool, PropertyInfo(alias="Disabled")]
    """``True`` 면 알림톡이 실패해도 문자를 보내지 않습니다 — 실패가 그대로 실패로 남습니다."""


class _MessageCreateBaseParams(TypedDict, total=False):
    to: Required[Annotated[str, PropertyInfo(alias="To")]]
    """수신 번호."""

    from_: Required[Annotated[str, PropertyInfo(alias="From")]]
    """발신 번호. 계정에 등록된 번호여야 합니다."""


class TextMessageCreateParams(_MessageCreateBaseParams, total=False):
    """문자(SMS/LMS/MMS) 발송 요청 파라미터."""

    body: Required[Annotated[str, PropertyInfo(alias="Body")]]
    """메시지 본문."""

    type: Annotated[TextMessageType, PropertyInfo(alias="Type")]
    """메시지 유형. 생략하면 본문 길이와 첨부 유무로 서버가 고릅니다.

    ⚠️ 통신사 SMS 상한은 **EUC-KR 90byte** 입니다. 이를 넘겨 ``"sms"`` 로 보내면
    ``400 body_too_long`` 입니다. 생략하면 긴 본문은 LMS 로 자동 발송됩니다.
    """

    subject: Annotated[str, PropertyInfo(alias="Subject")]
    """제목 (LMS/MMS 에서 사용)."""

    media_url: Annotated[list[str], PropertyInfo(alias="MediaUrl")]
    """첨부 이미지 URL 목록 (최대 3개, jpg/png/bmp, 장당 300KB 이하)."""


class KakaoMessageCreateParams(_MessageCreateBaseParams, total=False):
    """카카오 알림톡 발송 요청 파라미터.

    서버 규칙이 배타적입니다 — ``kakao`` 를 실으면 ``body``·``subject``·``media_url``
    은 금지이고 ``type`` 은 ``"ata"`` 만 허용됩니다. TypedDict 는 키 집합이 닫혀
    있으므로 그 규칙이 여기서 타입으로 강제됩니다: 문자 키를 섞으면 이쪽에도
    :class:`TextMessageCreateParams` 쪽에도 맞지 않아 타입 에러가 됩니다.
    """

    kakao: Required[KakaoSendParam]
    """알림톡 채널·템플릿·변수. 이 항목이 있으면 알림톡입니다."""

    fallback: KakaoFallbackParam
    """발송 실패 시 대신 나갈 문자. 생략하면 템플릿 본문을 그대로 보냅니다."""

    type: Literal["ata"]
    """생략해도 됩니다. 명시한다면 ``"ata"`` 여야 합니다."""


MessageCreateParams = Union[TextMessageCreateParams, KakaoMessageCreateParams]
"""메시지 발송 요청 파라미터. 문자와 알림톡 중 하나입니다."""


class MessageListParams(TypedDict, total=False):
    """메시지 목록 조회 요청 파라미터."""

    type: Union[TextMessageType, Literal["ata"]]
    """메시지 유형으로 필터링. ``"ata"`` 는 카카오 알림톡입니다."""

    status: Literal["queued", "sent", "failed", "received"]
    """메시지 상태로 필터링.

    ⚠️ 응답의 ``status`` 에는 ``"sending"`` 도 나올 수 있지만 **필터로는 쓸 수 없습니다** —
    서버 쿼리 검증이 위 네 가지만 받아 400 을 냅니다.
    """

    number: str
    """발신 또는 수신 번호로 필터링. 하이픈 유무를 모두 매칭합니다."""

    page: int
    """페이지 번호 (0부터 시작)."""

    page_size: Annotated[int, PropertyInfo(alias="pageSize")]
    """페이지당 항목 수 (기본 20, 최대 100)."""
