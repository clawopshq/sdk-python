from __future__ import annotations

from typing import Literal

from .._resource import AsyncAPIResource, SyncAPIResource
from .._utils import strip_not_given
from ..pagination import AsyncPage, SyncPage
from ..types.assignment_link import AssignmentLink, AssignmentLinkCreateResponse

LinkStatus = Literal["pending", "consumed", "expired", "revoked"]


class AssignmentLinks(SyncAPIResource):
    """관리번호(External Assignment) 발급 링크 리소스.

    POST/DELETE는 ``external_assignment`` 애드온이 활성화된 계정에서만 가능합니다.
    GET 조회는 애드온 활성 여부와 무관하게 가능합니다.
    """

    def create(
        self,
        *,
        webhook_url: str | None = None,
        webhook_method: Literal["POST", "GET"] | None = None,
        note: str | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> AssignmentLinkCreateResponse:
        """발급 링크를 생성합니다.

        Args:
            webhook_url: 발급된 번호로 인입되는 통화/메시지를 받을 webhook URL.
            webhook_method: 'POST' 또는 'GET' (기본 'POST').
            note: 내부 메모 (최대 200자).

        Raises:
            PermissionDeniedError: external_assignment 애드온 비활성 (403 FEATURE_DISABLED).
            UnprocessableEntityError: 활성 구독 없음 또는 슬롯 한도 초과.
            NotFoundError: 계정을 찾을 수 없음.
        """
        body = strip_not_given(
            {"webhookUrl": webhook_url, "webhookMethod": webhook_method, "note": note}
        )
        return self._client._post(
            f"{self._base_path}/assignment-links",
            body=body if body else None,
            cast_to=AssignmentLinkCreateResponse,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    def list(
        self,
        *,
        status: LinkStatus | None = None,
        page: int | None = None,
        page_size: int | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> SyncPage[AssignmentLink]:
        """발급 링크 목록을 조회합니다. ``auto_paging_iter()``로 전체 순회 가능."""
        query = strip_not_given({"status": status, "page": page, "pageSize": page_size})
        path = f"{self._base_path}/assignment-links"
        return self._client._get_page(
            path,
            cast_to=AssignmentLink,
            query=query,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    def retrieve(
        self,
        link_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> AssignmentLink:
        """특정 링크의 상세 정보를 조회합니다.

        Raises:
            NotFoundError: 링크를 찾을 수 없음.
        """
        return self._client._get(
            f"{self._base_path}/assignment-links/{link_id}",
            cast_to=AssignmentLink,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    def revoke(
        self,
        link_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> None:
        """pending 상태 링크를 취소합니다.

        Raises:
            ConflictError: 이미 소비/만료/취소된 링크 (409 NOT_PENDING).
        """
        self._client._delete(
            f"{self._base_path}/assignment-links/{link_id}",
            extra_headers=extra_headers,
            timeout=timeout,
        )


class AsyncAssignmentLinks(AsyncAPIResource):
    """관리번호 발급 링크 비동기 리소스."""

    async def create(
        self,
        *,
        webhook_url: str | None = None,
        webhook_method: Literal["POST", "GET"] | None = None,
        note: str | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> AssignmentLinkCreateResponse:
        body = strip_not_given(
            {"webhookUrl": webhook_url, "webhookMethod": webhook_method, "note": note}
        )
        return await self._client._post(
            f"{self._base_path}/assignment-links",
            body=body if body else None,
            cast_to=AssignmentLinkCreateResponse,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    async def list(
        self,
        *,
        status: LinkStatus | None = None,
        page: int | None = None,
        page_size: int | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> AsyncPage[AssignmentLink]:
        query = strip_not_given({"status": status, "page": page, "pageSize": page_size})
        path = f"{self._base_path}/assignment-links"
        return await self._client._get_page(
            path,
            cast_to=AssignmentLink,
            query=query,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    async def retrieve(
        self,
        link_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> AssignmentLink:
        return await self._client._get(
            f"{self._base_path}/assignment-links/{link_id}",
            cast_to=AssignmentLink,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    async def revoke(
        self,
        link_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> None:
        await self._client._delete(
            f"{self._base_path}/assignment-links/{link_id}",
            extra_headers=extra_headers,
            timeout=timeout,
        )
