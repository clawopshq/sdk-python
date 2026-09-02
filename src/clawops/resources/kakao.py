from __future__ import annotations

from .._resource import AsyncAPIResource, SyncAPIResource
from .._utils import strip_not_given
from ..pagination import AsyncPage, SyncPage
from ..types.kakao import (
    KakaoChannel,
    KakaoChannelCategoryList,
    KakaoChannelListStatus,
    KakaoTemplate,
    KakaoTokenRequest,
)

# sync/async 가 같은 문구를 쓰도록 한 벌만 둔다. 여기 적힌 경고는 async 사용자에게도
# 똑같이 필요하다 — 인증번호 소모나 템플릿 동반 삭제는 호출 방식과 무관하다.

_CHANNELS_DOC = """카카오 비즈니스 채널 리소스.

연결은 **두 단계**입니다. ``request_token()`` 으로 담당자 휴대전화에 인증번호를
보내고, 받은 번호를 ``connect()`` 에 넘깁니다. 서버는 그 사이 상태를 저장하지
않으므로(미완료 담당자 번호를 남기지 않기 위해) ``search_id`` 와 ``phone_number``
를 두 번 모두 보냅니다.
"""

_LIST_DOC = """연결된 채널 목록을 조회합니다. ``auto_paging_iter()`` 로 전체 순회 가능.

⚠️ **카카오 쪽 상태를 확인하지 않습니다.** ClawOps 에 저장된 연결 정보를 그대로
돌려주므로 빠릅니다. 실제 채널 상태까지 확인하려면 :meth:`retrieve` 를 쓰세요.

인증번호만 요청하고 연결을 완료하지 않은 시도는 채널로 잡히지 않습니다.

Args:
    status: 연결 상태 필터. 'connected' | 'needs_attention' | 'all'(기본).
    page: 페이지 번호 (0부터 시작).
    page_size: 페이지당 항목 수 (기본 20, 최대 100).

Returns:
    KakaoChannel 의 페이지. ``data[].id`` 가 발송의 ``kakao["channel_id"]`` 입니다.
"""

_RETRIEVE_DOC = """채널 하나를 조회합니다. **목록과 달리 카카오 쪽 상태를 실제로 확인하고 갱신합니다.**

``connect()`` 가 타임아웃됐을 때 결과를 확정하는 경로이기도 합니다 — 연결 요청을
재호출하면 중복 등록을 시도하게 되지만, 이 조회는 몇 번을 불러도 안전합니다.

카카오 쪽 조회에 실패해도 404 가 아닙니다. 연결 기록 자체는 유효하므로 ``status``
를 ``"needs_attention"`` 으로 표시해 돌려줍니다 — 실제로 끊겼는지 일시적 실패인지는
다시 호출해 확인하세요.

Args:
    channel_id: 채널 목록에서 받은 **ClawOps 채널 리소스 ID**. 검색용 ID 가 아닙니다.

Raises:
    NotFoundError: 이 계정에 연결된 채널이 아님.
"""

_REQUEST_TOKEN_DOC = """채널 권한 증명을 위한 인증번호를 담당자 휴대전화로 보내 달라고 요청합니다.

⚠️ **응답에 인증번호는 없습니다.** 인증번호는 카카오 비즈니스 채널에 등록된 담당자
휴대전화로만 전달되고 ClawOps 는 그 값을 받지도 저장하지도 않습니다. 그래서 성공이
200 이 아니라 202 입니다. 인증번호에는 유효 시간이 있으니 받은 뒤 바로
:meth:`connect` 로 진행하세요.

Args:
    search_id: 카카오 채널 검색용 ID. ``@`` 를 붙여 보내도 떼어내고 처리합니다.
    phone_number: 인증번호를 받을 담당자 휴대전화번호. 하이픈·+82 형태 모두 허용.
        **카카오 비즈니스 채널에 관리자로 등록된 번호여야** 발송됩니다.

Raises:
    BadRequestError: 형식 오류 또는 채널에 등록되지 않은 담당자 번호 (VALIDATION).
    RateLimitError: 재요청 제한 (KAKAO_RATE_LIMITED). ``retry_after_seconds`` 만큼 대기.
    ServiceUnavailableError: 카카오 채널 서비스 일시 장애 (KAKAO_PROVIDER_UNAVAILABLE).
"""

_CONNECT_DOC = """인증번호로 채널 연결을 완료합니다. **먼저 :meth:`request_token` 을 호출해야 합니다.**

**멱등입니다** — 이미 이 계정에 연결된 채널이면 인증번호를 소모하지 않고 기존 연결을
돌려줍니다(200). 새로 연결된 경우에만 201 입니다.

⛔ **타임아웃되면 이 요청을 다시 보내지 마세요.** 이미 연결에 성공했을 수 있어 중복
등록을 시도하게 됩니다. 대신 :meth:`retrieve` 나 :meth:`list` 로 실제 등록 여부를
확인한 뒤 결과를 확정하세요.

⚠️ **연결에 실패해도 인증번호는 소모됩니다** (KAKAO_TOKEN_INVALID ·
KAKAO_CHANNEL_REJECTED 둘 다). 원인을 해결한 뒤 인증번호를 새로 요청해야 합니다.
다만 429·503 은 연결이 **시도되지 않았다**는 뜻이라 인증번호가 아직 유효합니다.

Args:
    search_id: :meth:`request_token` 응답의 ``search_id`` 를 그대로 보냅니다.
    phone_number: 인증번호를 받은 담당자 휴대전화번호.
    category_code: :meth:`Kakao.channel_categories` 응답의 ``code``.
        **하드코딩하지 마세요** — 열린 집합입니다.
    token: 담당자 휴대전화로 받은 인증번호. 저장하지 않고 확인에만 씁니다.

Raises:
    BadRequestError: 검색용 ID·전화번호·카테고리 형식 오류 (VALIDATION).
    ConflictError: 이미 **다른 계정**에 연결된 채널 (KAKAO_CHANNEL_ALREADY_LINKED).
        재시도해도 결과가 같습니다.
    UnprocessableEntityError: 인증번호 불일치·만료(KAKAO_TOKEN_INVALID) 또는 채널이
        연동 요건 미달(KAKAO_CHANNEL_REJECTED — ``err.body["error"]`` 에 카카오 원문).
    RateLimitError: 공급자 호출량 제한 (KAKAO_RATE_LIMITED). 인증번호는 아직 유효합니다.
    ServiceUnavailableError: 카카오 채널 서비스 장애 (KAKAO_PROVIDER_UNAVAILABLE).
        **재호출하지 말고** :meth:`retrieve` 로 등록 여부를 확인하세요.
"""

_DISCONNECT_DOC = """채널 연동을 해제합니다. 카카오톡 채널 자체는 지워지지 않습니다.

⛔ **되돌릴 수 없고, 그 채널에 등록된 알림톡 템플릿도 함께 삭제됩니다.** 템플릿은
카카오 검수를 다시 받아야 하므로 복구에 시간이 걸립니다 — 호출 전에 사용자에게 이
사실을 알리고 확인을 받으세요. 이 API 는 확인 절차를 대신해 주지 않습니다.

해제 후에는 그 채널을 다시 연결할 수 있습니다(본인이든 다른 계정이든). 인증번호
요청부터 다시 시작하면 됩니다.

해제 요청은 감사 기록에 남습니다. API 키로 호출하면 키에는 사용자 정보가 없어
"누가" 는 남지 않습니다 — 담당자 단위 추적이 필요하면 대시보드에서 해제하세요.

Args:
    channel_id: ClawOps 채널 리소스 ID.

Returns:
    해제된 채널 정보.

Raises:
    NotFoundError: 이 계정에 연결된 채널이 아니거나 이미 해제됨.
    RateLimitError: 공급자 호출량 제한 — **해제되지 않았습니다.**
    ServiceUnavailableError: 카카오 채널 서비스 장애 — **해제되지 않았습니다.**
"""

_TEMPLATES_DOC = """알림톡 템플릿 리소스."""

_TEMPLATES_LIST_DOC = """채널에서 사용할 수 있는 알림톡 템플릿을 조회합니다.

``data[].id`` 를 발송의 ``kakao["template_id"]`` 로, ``data[].channel_id`` 를
``kakao["channel_id"]`` 로 씁니다.

⚠️ **``sendable`` 이 True 인 템플릿만 발송할 수 있습니다.** 카카오 검수 상태
(``status``)와 휴면 여부(``dormant``)를 서버가 합쳐 계산한 값이라 그쪽이 정본입니다.
``variables`` 의 항목은 발송 시 **모두** 채워야 합니다.

Args:
    channel_id: 채널 목록이 반환한 ClawOps 채널 리소스 ID. **필수**입니다.
    page: 페이지 번호 (0부터 시작).
    page_size: 페이지당 항목 수 (기본 20, 최대 100).

Raises:
    BadRequestError: channel_id 누락 또는 페이지 입력 오류 (VALIDATION).
    NotFoundError: 이 계정에 연결된 채널이 아님.
"""

_KAKAO_DOC = """카카오 알림톡 리소스 묶음.

    client.kakao.channels           # 채널 연결·조회·해제
    client.kakao.templates          # 템플릿 목록
    client.kakao.channel_categories()   # 연결 시 쓸 업종 카테고리

발송 자체는 ``client.messages.create(..., kakao={...})`` 입니다.
"""

_CATEGORIES_DOC = """채널 연결 시 지정할 업종 카테고리 목록입니다.

⚠️ **값을 코드에 하드코딩하지 마세요.** 카테고리는 카카오/공급자 쪽에서 늘거나
바뀌는 열린 집합이고, 이 응답이 그때그때의 정본입니다. ``code`` 를
:meth:`KakaoChannels.connect` 의 ``category_code`` 로 그대로 보냅니다.

페이지네이션이 없어 Page 가 아니라 전체를 한 번에 돌려줍니다.
"""


def _channel_list_query(
    *, status: KakaoChannelListStatus | None, page: int | None, page_size: int | None
) -> dict[str, object]:
    return strip_not_given({"status": status, "page": page, "pageSize": page_size})


def _template_list_query(
    *, channel_id: str, page: int | None, page_size: int | None
) -> dict[str, object]:
    return strip_not_given({"channelId": channel_id, "page": page, "pageSize": page_size})


def _connect_body(
    *, search_id: str, phone_number: str, category_code: str, token: str
) -> dict[str, object]:
    return {
        "searchId": search_id,
        "phoneNumber": phone_number,
        "categoryCode": category_code,
        "token": token,
    }


class KakaoChannels(SyncAPIResource):
    __doc__ = _CHANNELS_DOC

    @property
    def _path(self) -> str:
        return f"{self._base_path}/kakao/channels"

    def list(
        self,
        *,
        status: KakaoChannelListStatus | None = None,
        page: int | None = None,
        page_size: int | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> SyncPage[KakaoChannel]:
        query = _channel_list_query(status=status, page=page, page_size=page_size)
        return self._client._get_page(
            self._path, cast_to=KakaoChannel, query=query,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    list.__doc__ = _LIST_DOC

    def retrieve(
        self,
        channel_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> KakaoChannel:
        return self._client._get(
            f"{self._path}/{channel_id}", cast_to=KakaoChannel,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    retrieve.__doc__ = _RETRIEVE_DOC

    def request_token(
        self,
        *,
        search_id: str,
        phone_number: str,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> KakaoTokenRequest:
        return self._client._post(
            f"{self._path}/token",
            body={"searchId": search_id, "phoneNumber": phone_number},
            cast_to=KakaoTokenRequest,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    request_token.__doc__ = _REQUEST_TOKEN_DOC

    def connect(
        self,
        *,
        search_id: str,
        phone_number: str,
        category_code: str,
        token: str,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> KakaoChannel:
        return self._client._post(
            self._path,
            body=_connect_body(
                search_id=search_id, phone_number=phone_number,
                category_code=category_code, token=token,
            ),
            cast_to=KakaoChannel,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    connect.__doc__ = _CONNECT_DOC

    def disconnect(
        self,
        channel_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> KakaoChannel:
        return self._client._delete_with_response(
            f"{self._path}/{channel_id}", cast_to=KakaoChannel,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    disconnect.__doc__ = _DISCONNECT_DOC


class KakaoTemplates(SyncAPIResource):
    __doc__ = _TEMPLATES_DOC

    @property
    def _path(self) -> str:
        return f"{self._base_path}/kakao/templates"

    def list(
        self,
        *,
        channel_id: str,
        page: int | None = None,
        page_size: int | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> SyncPage[KakaoTemplate]:
        query = _template_list_query(channel_id=channel_id, page=page, page_size=page_size)
        return self._client._get_page(
            self._path, cast_to=KakaoTemplate, query=query,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    list.__doc__ = _TEMPLATES_LIST_DOC


class Kakao(SyncAPIResource):
    __doc__ = _KAKAO_DOC

    @property
    def channels(self) -> KakaoChannels:
        return KakaoChannels(client=self._client, account_id=self._account_id)

    @property
    def templates(self) -> KakaoTemplates:
        return KakaoTemplates(client=self._client, account_id=self._account_id)

    def channel_categories(
        self,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> KakaoChannelCategoryList:
        return self._client._get(
            f"{self._base_path}/kakao/channel-categories", cast_to=KakaoChannelCategoryList,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    channel_categories.__doc__ = _CATEGORIES_DOC


class AsyncKakaoChannels(AsyncAPIResource):
    __doc__ = _CHANNELS_DOC

    @property
    def _path(self) -> str:
        return f"{self._base_path}/kakao/channels"

    async def list(
        self,
        *,
        status: KakaoChannelListStatus | None = None,
        page: int | None = None,
        page_size: int | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> AsyncPage[KakaoChannel]:
        query = _channel_list_query(status=status, page=page, page_size=page_size)
        return await self._client._get_page(
            self._path, cast_to=KakaoChannel, query=query,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    list.__doc__ = _LIST_DOC

    async def retrieve(
        self,
        channel_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> KakaoChannel:
        return await self._client._get(
            f"{self._path}/{channel_id}", cast_to=KakaoChannel,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    retrieve.__doc__ = _RETRIEVE_DOC

    async def request_token(
        self,
        *,
        search_id: str,
        phone_number: str,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> KakaoTokenRequest:
        return await self._client._post(
            f"{self._path}/token",
            body={"searchId": search_id, "phoneNumber": phone_number},
            cast_to=KakaoTokenRequest,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    request_token.__doc__ = _REQUEST_TOKEN_DOC

    async def connect(
        self,
        *,
        search_id: str,
        phone_number: str,
        category_code: str,
        token: str,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> KakaoChannel:
        return await self._client._post(
            self._path,
            body=_connect_body(
                search_id=search_id, phone_number=phone_number,
                category_code=category_code, token=token,
            ),
            cast_to=KakaoChannel,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    connect.__doc__ = _CONNECT_DOC

    async def disconnect(
        self,
        channel_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> KakaoChannel:
        return await self._client._delete_with_response(
            f"{self._path}/{channel_id}", cast_to=KakaoChannel,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    disconnect.__doc__ = _DISCONNECT_DOC


class AsyncKakaoTemplates(AsyncAPIResource):
    __doc__ = _TEMPLATES_DOC

    @property
    def _path(self) -> str:
        return f"{self._base_path}/kakao/templates"

    async def list(
        self,
        *,
        channel_id: str,
        page: int | None = None,
        page_size: int | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> AsyncPage[KakaoTemplate]:
        query = _template_list_query(channel_id=channel_id, page=page, page_size=page_size)
        return await self._client._get_page(
            self._path, cast_to=KakaoTemplate, query=query,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    list.__doc__ = _TEMPLATES_LIST_DOC


class AsyncKakao(AsyncAPIResource):
    __doc__ = _KAKAO_DOC

    @property
    def channels(self) -> AsyncKakaoChannels:
        return AsyncKakaoChannels(client=self._client, account_id=self._account_id)

    @property
    def templates(self) -> AsyncKakaoTemplates:
        return AsyncKakaoTemplates(client=self._client, account_id=self._account_id)

    async def channel_categories(
        self,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> KakaoChannelCategoryList:
        return await self._client._get(
            f"{self._base_path}/kakao/channel-categories", cast_to=KakaoChannelCategoryList,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    channel_categories.__doc__ = _CATEGORIES_DOC
