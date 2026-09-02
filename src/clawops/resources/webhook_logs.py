from __future__ import annotations

from .._resource import AsyncAPIResource, SyncAPIResource
from .._utils import strip_not_given
from ..pagination import AsyncPage, SyncPage
from ..types.webhook_log import WebhookLog


class WebhookLogs(SyncAPIResource):
    """Webhook 발송 로그 리소스."""

    def list(
        self,
        webhook_id: str,
        *,
        page: int | None = None,
        page_size: int | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> SyncPage[WebhookLog]:
        """Webhook 발송 로그를 조회합니다.

        ``auto_paging_iter()``를 사용하면 모든 페이지를 자동으로 순회할 수 있습니다.

        Args:
            webhook_id: Webhook ID.
            page: 페이지 번호 (0부터 시작, 기본값 0).
            page_size: 페이지당 항목 수 (기본 20, 최대 100).
            extra_headers: 추가 HTTP 헤더.
            extra_query: 추가 쿼리 파라미터.
            timeout: 이 요청의 타임아웃 (초).

        Returns:
            WebhookLog 객체의 페이지. ``auto_paging_iter()``로 전체 순회 가능.
        """
        query = strip_not_given({"page": page, "pageSize": page_size})
        path = f"{self._base_path}/webhooks/{webhook_id}/logs"
        return self._client._get_page(
            path, cast_to=WebhookLog, query=query,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )


class AsyncWebhookLogs(AsyncAPIResource):
    """Webhook 발송 로그 비동기 리소스."""

    async def list(
        self,
        webhook_id: str,
        *,
        page: int | None = None,
        page_size: int | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> AsyncPage[WebhookLog]:
        """Webhook 발송 로그를 비동기로 조회합니다."""
        query = strip_not_given({"page": page, "pageSize": page_size})
        path = f"{self._base_path}/webhooks/{webhook_id}/logs"
        return await self._client._get_page(
            path, cast_to=WebhookLog, query=query,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )
