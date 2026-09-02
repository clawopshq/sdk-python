from __future__ import annotations

from typing import Any, Generic, Iterator, AsyncIterator, TypeVar, TYPE_CHECKING

import pydantic

from ._models import BaseModel
from .types.shared import PaginationMeta

if TYPE_CHECKING:
    from ._base_client import AsyncAPIClient, SyncAPIClient

_T = TypeVar("_T", bound=pydantic.BaseModel)

class SyncPage(BaseModel, Generic[_T]):
    """동기 페이지네이션 컨테이너."""

    data: list[Any]
    meta: PaginationMeta

    _client: SyncAPIClient | None = pydantic.PrivateAttr(default=None)
    _path: str = pydantic.PrivateAttr(default="")
    _cast_to: type[_T] | None = pydantic.PrivateAttr(default=None)
    _query: dict[str, Any] = pydantic.PrivateAttr(default_factory=dict)

    def _set_client(self, *, client: SyncAPIClient, path: str, cast_to: type[_T], query: dict[str, Any]) -> None:
        self._client = client
        self._path = path
        self._cast_to = cast_to
        self._query = query

    def has_next_page(self) -> bool:
        return (self.meta.page + 1) * self.meta.page_size < self.meta.total

    def next_page(self) -> SyncPage[_T]:
        if self._client is None:
            raise RuntimeError("Page client is not set")
        if not self.has_next_page():
            raise StopIteration("No more pages")
        if self._cast_to is None:
            raise RuntimeError("Page cast_to is not set")
        next_query = self._query.copy()
        next_query["page"] = self.meta.page + 1
        # 아이템 검증은 클라이언트 안에서 돈다 — 뒷장에서 raw pydantic 예외가 새면
        # auto_paging_iter() 중간에 ClawOpsError 밖의 예외로 순회가 끊긴다.
        result = self._client._get_page(self._path, cast_to=self._cast_to, query=next_query)
        result._set_client(client=self._client, path=self._path, cast_to=self._cast_to, query=self._query)
        return result

    def __iter__(self) -> Iterator[_T]:
        return iter(self.data)

    def auto_paging_iter(self) -> Iterator[_T]:
        page: SyncPage[_T] = self
        while True:
            yield from page.data
            if not page.has_next_page():
                break
            page = page.next_page()


class AsyncPage(BaseModel, Generic[_T]):
    """비동기 페이지네이션 컨테이너."""

    data: list[Any]
    meta: PaginationMeta

    _client: AsyncAPIClient | None = pydantic.PrivateAttr(default=None)
    _path: str = pydantic.PrivateAttr(default="")
    _cast_to: type[_T] | None = pydantic.PrivateAttr(default=None)
    _query: dict[str, Any] = pydantic.PrivateAttr(default_factory=dict)

    def _set_client(self, *, client: AsyncAPIClient, path: str, cast_to: type[_T], query: dict[str, Any]) -> None:
        self._client = client
        self._path = path
        self._cast_to = cast_to
        self._query = query

    def has_next_page(self) -> bool:
        return (self.meta.page + 1) * self.meta.page_size < self.meta.total

    async def next_page(self) -> AsyncPage[_T]:
        if self._client is None:
            raise RuntimeError("Page client is not set")
        if not self.has_next_page():
            raise StopAsyncIteration("No more pages")
        if self._cast_to is None:
            raise RuntimeError("Page cast_to is not set")
        next_query = self._query.copy()
        next_query["page"] = self.meta.page + 1
        result = await self._client._get_page(self._path, cast_to=self._cast_to, query=next_query)
        result._set_client(client=self._client, path=self._path, cast_to=self._cast_to, query=self._query)
        return result

    def __iter__(self) -> Iterator[_T]:
        return iter(self.data)

    async def auto_paging_iter(self) -> AsyncIterator[_T]:
        page: AsyncPage[_T] = self
        while True:
            for item in page.data:
                yield item
            if not page.has_next_page():
                break
            page = await page.next_page()
