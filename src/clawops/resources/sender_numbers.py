from __future__ import annotations

from .._resource import AsyncAPIResource, SyncAPIResource
from ..types.sender_number import SenderNumber, SenderNumberList

_DOC = """문자 발신번호 리소스(clawops#1489).

문자(SMS·LMS·MMS)는 계정에 등록해 승인된 발신번호로만 보낼 수 있습니다. 승인되지 않은 번호를
``from_`` 으로 보내면 ``400 from_not_registered`` 로 거절됩니다.

번호를 발급(``numbers.create()``)하면 회선 수 한도 안에서 자동으로 등록되며, 결과는 발급 응답의
``sms_sender`` 에 실립니다.

.. warning::
   발신번호 범위를 지정한 API 키는 조회만 됩니다 — 등록·삭제는 ``403 api_key_sender_numbers_restricted``.
"""

_CREATE_DOC = """이 계정이 ClawOps에서 개통한 070번호·대표번호를 발신번호로 등록합니다. 등록 즉시 승인됩니다.

        한 번에 한 번호씩 등록하며 하이픈·``+82`` 표기도 받습니다.

        Args:
            number: 등록할 번호.

        Raises:
            ConflictError: 이미 등록됨(``sender_number_already_registered`` — 이미 발송에 쓸 수 있는 상태),
                한도 미정(``sender_number_limit_unknown``), 명의 확인 불가(``sender_number_holder_unresolved``).
            UnprocessableEntityError: 회선 수 한도 도달(``sender_number_limit_reached``),
                등록할 수 없는 번호(``sender_number_not_registrable``).
            NotFoundError: 이 계정이 개통한 번호가 아님(``sender_number_not_account_number``).
            PermissionDeniedError: 발신번호 범위를 지정한 API 키(``api_key_sender_numbers_restricted``).
        """

_DELETE_DOC = """발신번호를 삭제합니다. 그 번호로는 바로 문자를 보낼 수 없고(유예도 함께 끝남) 회선 수
        한도에서 빠집니다. 번호 자체는 반납되지 않습니다.

        API 로는 ClawOps에서 개통한 번호(``verification_method == 'own_provisioned'``)만 삭제할 수 있습니다.

        Raises:
            ConflictError: API 로 삭제할 수 없는 발신번호(``sender_number_not_deletable_via_api``) — 콘솔에서 삭제.
            NotFoundError: 발신번호 없음(``sender_number_not_found``).
            PermissionDeniedError: 발신번호 범위를 지정한 API 키(``api_key_sender_numbers_restricted``).
        """


class SenderNumbers(SyncAPIResource):
    __doc__ = _DOC

    def list(
        self,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> SenderNumberList:
        """등록된 발신번호·유예 기간인 번호·070번호·대표번호 회선 수 한도를 함께 돌려줍니다.

        발신번호 범위를 지정한 API 키로 호출하면 그 범위 안의 번호만 보입니다.
        """
        return self._client._get(
            f"{self._base_path}/sender-numbers",
            cast_to=SenderNumberList,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    def create(
        self,
        *,
        number: str,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> SenderNumber:
        return self._client._post(
            f"{self._base_path}/sender-numbers",
            body={"number": number},
            cast_to=SenderNumber,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    create.__doc__ = _CREATE_DOC

    def delete(
        self,
        sender_number_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> None:
        self._client._delete(
            f"{self._base_path}/sender-numbers/{sender_number_id}",
            extra_headers=extra_headers,
            timeout=timeout,
        )

    delete.__doc__ = _DELETE_DOC


class AsyncSenderNumbers(AsyncAPIResource):
    __doc__ = _DOC

    async def list(
        self,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> SenderNumberList:
        """등록된 발신번호·유예 기간인 번호·회선 수 한도(sync 쪽 ``list`` 와 같다)."""
        return await self._client._get(
            f"{self._base_path}/sender-numbers",
            cast_to=SenderNumberList,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    async def create(
        self,
        *,
        number: str,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> SenderNumber:
        return await self._client._post(
            f"{self._base_path}/sender-numbers",
            body={"number": number},
            cast_to=SenderNumber,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    create.__doc__ = _CREATE_DOC

    async def delete(
        self,
        sender_number_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> None:
        await self._client._delete(
            f"{self._base_path}/sender-numbers/{sender_number_id}",
            extra_headers=extra_headers,
            timeout=timeout,
        )

    delete.__doc__ = _DELETE_DOC
