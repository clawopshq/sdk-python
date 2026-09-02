from __future__ import annotations

from typing import Any, Literal, Optional, Union, overload

from .._resource import AsyncAPIResource, SyncAPIResource
from .._utils import strip_not_given
from ..pagination import AsyncPage, SyncPage
from ..types.message import Message
from ..types.message_params import KakaoFallbackParam, KakaoSendParam, TextMessageType

_LIST_TYPE = Optional[Union[TextMessageType, Literal["ata"]]]
_LIST_STATUS = Optional[Literal["queued", "sent", "failed", "received"]]

_CREATE_DOC = """메시지를 발송합니다. **문자와 알림톡 두 가지 형태**입니다.

문자(SMS/LMS/MMS)는 ``body`` 를, 카카오 알림톡은 ``kakao`` 를 줍니다. 둘은 섞을 수
없습니다 — 서버가 배타적으로 검증하므로 오버로드로 미리 막습니다.

Args:
    to: 수신 번호.
    from_: 발신 번호. 계정에 등록된 번호여야 합니다.
    body: 메시지 본문 (문자 전용).
    type: 메시지 유형. 문자는 sms/lms/mms, 알림톡은 ata(생략 가능).
        ⚠️ 통신사 SMS 상한은 EUC-KR 90byte 입니다. 넘겨서 ``"sms"`` 로 보내면
        ``400 body_too_long`` 이고, 생략하면 긴 본문이 LMS 로 자동 발송됩니다.
    subject: 제목 (LMS/MMS 전용).
    media_url: 첨부 이미지 URL 목록 (MMS 전용, 최대 3개).
    kakao: 알림톡 채널·템플릿·변수. 이 값을 주면 알림톡입니다.
        ``channel_id`` 는 ``client.kakao.channels.list()``, ``template_id`` 는
        ``client.kakao.templates.list(channel_id=...)`` 로 얻습니다.
    fallback: 알림톡 발송 실패 시 대신 나갈 문자. 생략하면 템플릿 본문을 그대로
        보냅니다. **대체 발송은 별도 메시지 1건으로 문자 단가가 따로 청구됩니다.**
    extra_headers: 추가 HTTP 헤더.
    extra_query: 추가 쿼리 파라미터.
    timeout: 이 요청의 타임아웃 (초).

Returns:
    생성된 Message 객체. 알림톡이면 ``type`` 이 ``"ata"`` 이고 ``body`` 에는
    템플릿에 변수를 치환한 결과가 담깁니다. 버튼·아이템 리스트·강조 문구는
    템플릿에 검수된 대로 발송되며 요청으로 바꿀 수 없습니다.

Raises:
    TypeError: 문자 인자와 알림톡 인자를 섞었거나 둘 다 주지 않은 경우.
    BadRequestError: 템플릿 변수 누락(``kakao_variable_missing``)·초과
        (``kakao_variable_unknown``) 등. 사유는 ``err.code`` 로 분기합니다.
"""

_LIST_DOC = """메시지 목록을 조회합니다. ``auto_paging_iter()`` 로 전체 순회 가능.

Args:
    type: 메시지 유형으로 필터링. ``"ata"`` 는 카카오 알림톡입니다.
    status: 메시지 상태로 필터링.
        ⚠️ 응답에는 ``"sending"`` 도 나오지만 **필터로는 쓸 수 없습니다** —
        서버 쿼리 검증이 위 네 가지만 받아 400 을 냅니다.
    number: 발신 또는 수신 번호로 필터링. 하이픈 유무를 모두 매칭합니다.
    page: 페이지 번호 (0부터 시작, 기본값 0).
    page_size: 페이지당 항목 수 (기본 20, 최대 100).
    extra_headers: 추가 HTTP 헤더.
    extra_query: 추가 쿼리 파라미터.
    timeout: 이 요청의 타임아웃 (초).

Returns:
    Message 객체의 페이지.
"""


def _build_create_body(
    *,
    to: str,
    from_: str,
    body: str | None,
    type: str | None,
    subject: str | None,
    media_url: list[str] | None,
    kakao: KakaoSendParam | None,
    fallback: KakaoFallbackParam | None,
) -> dict[str, Any]:
    """발송 요청 body 를 조립한다. sync/async 가 같은 규칙을 쓰도록 한 곳에 둔다.

    오버로드는 타입체커를 돌리는 사람에게만 보인다. 여기서 한 번 더 거절하는 이유는
    mypy 없이 쓰는 사용자에게 400 대신 무엇이 잘못됐는지 알려주기 위해서다.
    """
    if kakao is not None:
        wrong = [n for n, v in (("body", body), ("subject", subject), ("media_url", media_url)) if v is not None]
        if wrong:
            raise TypeError(
                f"알림톡과 함께 보낼 수 없는 인자입니다: {', '.join(wrong)}. "
                "본문은 검수된 템플릿에서 오고, 문자로 대신 보낼 내용은 fallback 에 넣습니다."
            )
        if type is not None and type != "ata":
            raise TypeError(f"kakao 를 주면 type 은 'ata' 여야 합니다 (받은 값: {type!r}).")
    else:
        if fallback is not None:
            raise TypeError("fallback 은 알림톡 전용입니다. kakao 와 함께 지정하세요.")
        if body is None:
            raise TypeError("body(문자) 또는 kakao(알림톡) 중 하나는 반드시 지정해야 합니다.")

    # ⚠️ strip_not_given 은 얕다. 중첩 객체는 손으로 조립한다.
    return strip_not_given(
        {
            "To": to,
            "From": from_,
            "Body": body,
            "Type": type,
            "Subject": subject,
            "MediaUrl": media_url,
            "Kakao": None
            if kakao is None
            else strip_not_given(
                {
                    "ChannelId": kakao["channel_id"],
                    "TemplateId": kakao["template_id"],
                    "Variables": kakao.get("variables"),
                }
            ),
            "Fallback": None
            if fallback is None
            else strip_not_given(
                {
                    "Type": fallback.get("type"),
                    "Subject": fallback.get("subject"),
                    "Body": fallback.get("body"),
                    "Disabled": fallback.get("disabled"),
                }
            ),
        }
    )


def _build_list_query(
    *,
    type: _LIST_TYPE,
    status: _LIST_STATUS,
    number: str | None,
    page: int | None,
    page_size: int | None,
) -> dict[str, Any]:
    return strip_not_given(
        {"type": type, "status": status, "number": number, "page": page, "pageSize": page_size}
    )


class Messages(SyncAPIResource):
    """메시지(Messages) 리소스. 문자·알림톡 발송, 목록 조회, 단건 조회."""

    @overload
    def create(
        self,
        *,
        to: str,
        from_: str,
        body: str,
        type: TextMessageType | None = None,
        subject: str | None = None,
        media_url: list[str] | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> Message: ...

    @overload
    def create(
        self,
        *,
        to: str,
        from_: str,
        kakao: KakaoSendParam,
        fallback: KakaoFallbackParam | None = None,
        type: Literal["ata"] | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> Message: ...

    def create(
        self,
        *,
        to: str,
        from_: str,
        body: str | None = None,
        type: TextMessageType | Literal["ata"] | None = None,
        subject: str | None = None,
        media_url: list[str] | None = None,
        kakao: KakaoSendParam | None = None,
        fallback: KakaoFallbackParam | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> Message:
        req_body = _build_create_body(
            to=to, from_=from_, body=body, type=type, subject=subject,
            media_url=media_url, kakao=kakao, fallback=fallback,
        )
        return self._client._post(
            f"{self._base_path}/messages", body=req_body, cast_to=Message,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    create.__doc__ = _CREATE_DOC

    def list(
        self,
        *,
        type: _LIST_TYPE = None,
        status: _LIST_STATUS = None,
        number: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> SyncPage[Message]:
        query = _build_list_query(
            type=type, status=status, number=number, page=page, page_size=page_size
        )
        path = f"{self._base_path}/messages"
        return self._client._get_page(
            path, cast_to=Message, query=query,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    list.__doc__ = _LIST_DOC

    def get(
        self,
        message_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> Message:
        """특정 메시지의 상세 정보를 조회합니다.

        Args:
            message_id: 메시지 ID (예: 'MG0123456789abcdef...').
            extra_headers: 추가 HTTP 헤더.
            extra_query: 추가 쿼리 파라미터.
            timeout: 이 요청의 타임아웃 (초).

        Returns:
            Message 객체.
        """
        return self._client._get(
            f"{self._base_path}/messages/{message_id}", cast_to=Message,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )


class AsyncMessages(AsyncAPIResource):
    """메시지(Messages) 비동기 리소스. Messages의 async 버전."""

    @overload
    async def create(
        self,
        *,
        to: str,
        from_: str,
        body: str,
        type: TextMessageType | None = None,
        subject: str | None = None,
        media_url: list[str] | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> Message: ...

    @overload
    async def create(
        self,
        *,
        to: str,
        from_: str,
        kakao: KakaoSendParam,
        fallback: KakaoFallbackParam | None = None,
        type: Literal["ata"] | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> Message: ...

    async def create(
        self,
        *,
        to: str,
        from_: str,
        body: str | None = None,
        type: TextMessageType | Literal["ata"] | None = None,
        subject: str | None = None,
        media_url: list[str] | None = None,
        kakao: KakaoSendParam | None = None,
        fallback: KakaoFallbackParam | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> Message:
        req_body = _build_create_body(
            to=to, from_=from_, body=body, type=type, subject=subject,
            media_url=media_url, kakao=kakao, fallback=fallback,
        )
        return await self._client._post(
            f"{self._base_path}/messages", body=req_body, cast_to=Message,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    create.__doc__ = _CREATE_DOC

    async def list(
        self,
        *,
        type: _LIST_TYPE = None,
        status: _LIST_STATUS = None,
        number: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> AsyncPage[Message]:
        query = _build_list_query(
            type=type, status=status, number=number, page=page, page_size=page_size
        )
        path = f"{self._base_path}/messages"
        return await self._client._get_page(
            path, cast_to=Message, query=query,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    list.__doc__ = _LIST_DOC

    async def get(self, message_id: str, *, extra_headers: dict[str, str] | None = None,
                  extra_query: dict[str, object] | None = None, timeout: float | None = None) -> Message:
        """특정 메시지를 비동기로 조회합니다."""
        return await self._client._get(
            f"{self._base_path}/messages/{message_id}", cast_to=Message,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )
