from __future__ import annotations

from .._resource import AsyncAPIResource, SyncAPIResource
from .._utils import strip_not_given
from ..pagination import AsyncPage, SyncPage
from ..types.blocked_recipient import (
    BlockedChannel,
    BlockedRecipient,
    BlockedRecipientSource,
    BlockedRecipientStatus,
)

_DOC = """수신거부(DNC) 명단 리소스.

등록된 번호는 이 계정의 **발신**(전화·문자)에서 제외됩니다. 착신은 막지 않습니다 —
그 번호에서 걸려오는 전화는 그대로 받습니다.

전화와 문자는 각각 따로 차단합니다. 같은 번호라도 채널마다 별개 항목이므로,
둘 다 막으려면 ``channel`` 을 바꿔 두 번 등록합니다.
"""


class BlockedRecipients(SyncAPIResource):
    __doc__ = _DOC

    def create(
        self,
        *,
        number: str,
        channel: BlockedChannel,
        source: BlockedRecipientSource | None = None,
        source_ref: str | None = None,
        note: str | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> BlockedRecipient:
        """번호를 수신거부 명단에 등록합니다.

        하이픈·``+82`` 표기 모두 허용되며 국내 표기로 정규화되어 저장됩니다.

        **멱등입니다** — 이미 차단 중인 (번호, 채널)을 다시 등록해도 에러가 아니라 기존
        항목을 돌려줍니다. 같은 사람이 수신거부를 두 번 요청하는 것은 정상 상황입니다.

        Args:
            number: 수신거부할 번호. '010-1234-5678', '+821012345678' 모두 가능.
            channel: 'call'(전화) 또는 'message'(문자 — SMS/LMS/MMS 공통).
            source: 접수 경로. 'api'(기본) | 'console' | 'import'.
            source_ref: 증빙 링크(통화 id 또는 메시지 id).
            note: 자유 메모 (최대 500자).

        Raises:
            BadRequestError: 번호 형식 오류, 잘못된 channel/source (400 VALIDATION).
        """
        body = strip_not_given(
            {
                "number": number,
                "channel": channel,
                "source": source,
                "sourceRef": source_ref,
                "note": note,
            }
        )
        return self._client._post(
            f"{self._base_path}/blocked-recipients",
            body=body,
            cast_to=BlockedRecipient,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    def list(
        self,
        *,
        channel: BlockedChannel | None = None,
        number: str | None = None,
        status: BlockedRecipientStatus | None = None,
        page: int | None = None,
        page_size: int | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> SyncPage[BlockedRecipient]:
        """수신거부 목록을 조회합니다. ``auto_paging_iter()`` 로 전체 순회 가능.

        기본은 **현재 차단 중인 항목만** 이며, 해제 이력까지 보려면 ``status`` 를
        'released' 또는 'all' 로 지정합니다. ``number`` 는 하이픈 표기로 넣어도
        정규화 후 대조합니다.
        """
        query = strip_not_given(
            {
                "channel": channel,
                "number": number,
                "status": status,
                "page": page,
                "pageSize": page_size,
            }
        )
        path = f"{self._base_path}/blocked-recipients"
        result = self._client._get(
            path,
            cast_to=SyncPage[BlockedRecipient],
            query=query if query else None,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )
        result.data = [
            BlockedRecipient.model_validate(item) if isinstance(item, dict) else item
            for item in result.data
        ]
        result._set_client(client=self._client, path=path, cast_to=BlockedRecipient, query=query)
        return result

    def retrieve(
        self,
        block_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> BlockedRecipient:
        """항목 상세를 조회합니다. 해제된 항목도 이력으로 남아 조회됩니다(``active=False``).

        Raises:
            NotFoundError: 항목을 찾을 수 없음.
        """
        return self._client._get(
            f"{self._base_path}/blocked-recipients/{block_id}",
            cast_to=BlockedRecipient,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    def update(
        self,
        block_id: str,
        *,
        note: str | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> BlockedRecipient:
        """메모를 수정합니다. ``note=None`` 이면 메모를 지웁니다.

        번호와 채널은 바꿀 수 없습니다 — 바꾸면 "누가 무엇을 언제 거부했는가"라는 증빙이
        뒤틀립니다. 잘못 등록했다면 해제 후 올바른 번호로 새로 등록하세요.
        """
        return self._client._patch(
            f"{self._base_path}/blocked-recipients/{block_id}",
            body={"note": note},
            cast_to=BlockedRecipient,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    def release(
        self,
        block_id: str,
        *,
        note: str | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> BlockedRecipient:
        """수신거부를 해제해 다시 발신할 수 있게 합니다.

        **항목은 삭제되지 않습니다.** ``active`` 가 False 가 되고 ``unblocked_at`` 이
        기록될 뿐, 행은 이력으로 남습니다 — 언제 거부했고 언제 풀렸는지가 곧 증빙이기
        때문입니다. 해제분은 ``list(status="released")`` 로 볼 수 있습니다.

        이미 해제된 항목에 다시 호출해도 성공하며, 최초 해제 시각은 덮어쓰지 않습니다.
        """
        body = strip_not_given({"note": note})
        return self._client._delete_with_response(
            f"{self._base_path}/blocked-recipients/{block_id}",
            body=body if body else None,
            cast_to=BlockedRecipient,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )


class AsyncBlockedRecipients(AsyncAPIResource):
    """수신거부(DNC) 명단 비동기 리소스."""

    async def create(
        self,
        *,
        number: str,
        channel: BlockedChannel,
        source: BlockedRecipientSource | None = None,
        source_ref: str | None = None,
        note: str | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> BlockedRecipient:
        body = strip_not_given(
            {
                "number": number,
                "channel": channel,
                "source": source,
                "sourceRef": source_ref,
                "note": note,
            }
        )
        return await self._client._post(
            f"{self._base_path}/blocked-recipients",
            body=body,
            cast_to=BlockedRecipient,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    async def list(
        self,
        *,
        channel: BlockedChannel | None = None,
        number: str | None = None,
        status: BlockedRecipientStatus | None = None,
        page: int | None = None,
        page_size: int | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> AsyncPage[BlockedRecipient]:
        query = strip_not_given(
            {
                "channel": channel,
                "number": number,
                "status": status,
                "page": page,
                "pageSize": page_size,
            }
        )
        path = f"{self._base_path}/blocked-recipients"
        result = await self._client._get(
            path,
            cast_to=AsyncPage[BlockedRecipient],
            query=query if query else None,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )
        result.data = [
            BlockedRecipient.model_validate(item) if isinstance(item, dict) else item
            for item in result.data
        ]
        result._set_client(client=self._client, path=path, cast_to=BlockedRecipient, query=query)
        return result

    async def retrieve(
        self,
        block_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> BlockedRecipient:
        return await self._client._get(
            f"{self._base_path}/blocked-recipients/{block_id}",
            cast_to=BlockedRecipient,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    async def update(
        self,
        block_id: str,
        *,
        note: str | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> BlockedRecipient:
        return await self._client._patch(
            f"{self._base_path}/blocked-recipients/{block_id}",
            body={"note": note},
            cast_to=BlockedRecipient,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    async def release(
        self,
        block_id: str,
        *,
        note: str | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> BlockedRecipient:
        body = strip_not_given({"note": note})
        return await self._client._delete_with_response(
            f"{self._base_path}/blocked-recipients/{block_id}",
            body=body if body else None,
            cast_to=BlockedRecipient,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )
