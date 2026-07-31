from __future__ import annotations

from typing import TYPE_CHECKING

from .assignment_links import AssignmentLinks, AsyncAssignmentLinks
from .blocked_recipients import AsyncBlockedRecipients, BlockedRecipients
from .calls import AsyncCalls, Calls
from .messages import AsyncMessages, Messages
from .numbers import AsyncNumbers, Numbers
from .recordings import AsyncRecordings, Recordings
from .webhook_logs import AsyncWebhookLogs, WebhookLogs

if TYPE_CHECKING:
    from .._base_client import AsyncAPIClient, SyncAPIClient


class AccountContext:
    """특정 계정에 바인딩된 리소스 컨텍스트.

    Example::

        other = client.accounts("AC_other_id")
        other.calls.list()
        other.numbers.create()
    """

    def __init__(self, client: SyncAPIClient, account_id: str) -> None:
        self._client = client
        self._account_id = account_id

    @property
    def calls(self) -> Calls:
        return Calls(client=self._client, account_id=self._account_id)

    @property
    def messages(self) -> Messages:
        return Messages(client=self._client, account_id=self._account_id)

    @property
    def numbers(self) -> Numbers:
        return Numbers(client=self._client, account_id=self._account_id)

    @property
    def recordings(self) -> Recordings:
        return Recordings(client=self._client, account_id=self._account_id)

    @property
    def webhook_logs(self) -> WebhookLogs:
        return WebhookLogs(client=self._client, account_id=self._account_id)

    @property
    def assignment_links(self) -> AssignmentLinks:
        return AssignmentLinks(client=self._client, account_id=self._account_id)

    @property
    def blocked_recipients(self) -> BlockedRecipients:
        return BlockedRecipients(client=self._client, account_id=self._account_id)


class AsyncAccountContext:
    """비동기 계정 컨텍스트."""

    def __init__(self, client: AsyncAPIClient, account_id: str) -> None:
        self._client = client
        self._account_id = account_id

    @property
    def calls(self) -> AsyncCalls:
        return AsyncCalls(client=self._client, account_id=self._account_id)

    @property
    def messages(self) -> AsyncMessages:
        return AsyncMessages(client=self._client, account_id=self._account_id)

    @property
    def numbers(self) -> AsyncNumbers:
        return AsyncNumbers(client=self._client, account_id=self._account_id)

    @property
    def recordings(self) -> AsyncRecordings:
        return AsyncRecordings(client=self._client, account_id=self._account_id)

    @property
    def webhook_logs(self) -> AsyncWebhookLogs:
        return AsyncWebhookLogs(client=self._client, account_id=self._account_id)

    @property
    def assignment_links(self) -> AsyncAssignmentLinks:
        return AsyncAssignmentLinks(client=self._client, account_id=self._account_id)

    @property
    def blocked_recipients(self) -> AsyncBlockedRecipients:
        return AsyncBlockedRecipients(client=self._client, account_id=self._account_id)
