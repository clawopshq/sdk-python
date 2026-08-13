from __future__ import annotations

from typing import Literal

from .._resource import AsyncAPIResource, SyncAPIResource
from .._utils import strip_not_given
from ..types.number import NumberListItem, NumberUpdateResponse, PhoneNumber, RoutingType


def _create_body(
    webhook_url: str | None,
    webhook_method: Literal["POST", "GET"] | None,
    webhook_headers: dict[str, str] | None,
    status_callback: str | None,
    status_callback_events: str | None,
) -> dict[str, object]:
    return strip_not_given({
        "webhookUrl": webhook_url,
        "webhookMethod": webhook_method,
        "webhookHeaders": webhook_headers,
        "statusCallback": status_callback,
        "statusCallbackEvents": status_callback_events,
    })


def _update_body(
    webhook_url: str | None,
    webhook_method: Literal["POST", "GET"] | None,
    webhook_headers: dict[str, str] | None,
    call_context_url: str | None,
    routing_type: RoutingType | None,
    agent_id: str | None,
    call_flow_id: str | None,
    forward_to: str | None,
    sip_endpoint_id: str | None,
    sip_credential_id: str | None,
    status_callback: str | None,
    status_callback_events: str | None,
    dictionary_id: str | None,
) -> dict[str, object]:
    return strip_not_given({
        "routingType": routing_type,
        "agentId": agent_id,
        "callFlowId": call_flow_id,
        "forwardTo": forward_to,
        "sipEndpointId": sip_endpoint_id,
        "sipCredentialId": sip_credential_id,
        "webhookUrl": webhook_url,
        "webhookMethod": webhook_method,
        "webhookHeaders": webhook_headers,
        "callContextUrl": call_context_url,
        "statusCallback": status_callback,
        "statusCallbackEvents": status_callback_events,
        "dictionaryId": dictionary_id,
    })


class Numbers(SyncAPIResource):
    """전화번호(Numbers) 리소스."""

    def create(self, *, webhook_url: str | None = None,
               webhook_method: Literal["POST", "GET"] | None = None,
               webhook_headers: dict[str, str] | None = None,
               status_callback: str | None = None,
               status_callback_events: str | None = None,
               extra_headers: dict[str, str] | None = None,
               extra_query: dict[str, object] | None = None, timeout: float | None = None) -> PhoneNumber:
        """PSTN 번호를 발급합니다.

        번호 풀에서 자동으로 번호를 발급합니다. 어떤 번호가 배정될지는 지정할 수 없습니다.

        발급 직후 번호는 routing_type='webhook' 이고 webhook_url 이 비어 있어, 그대로 두면
        걸려온 전화가 거절됩니다. 이어서 update() 로 착신 라우팅을 지정하세요.

        Args:
            webhook_url: 수신 전화 처리용 Webhook URL.
            webhook_method: 'POST' 또는 'GET'.
            webhook_headers: Webhook 호출 시 덧붙일 헤더. 키는 'X-' 로 시작해야 합니다.
            status_callback: 수신 통화 상태 통지 URL.
            status_callback_events: 구독할 상태 이벤트(공백 구분).
            extra_headers: 추가 HTTP 헤더.
            extra_query: 추가 쿼리 파라미터.
            timeout: 이 요청의 타임아웃 (초).

        Returns:
            등록된 PhoneNumber 객체.

        Raises:
            ConflictError: 법인 인증 미완료.
            UnprocessableEntityError: 번호 할당량 초과.
            ServiceUnavailableError: 발급 가능한 번호 없음.
        """
        body = _create_body(webhook_url, webhook_method, webhook_headers,
                            status_callback, status_callback_events)
        return self._client._post(
            f"{self._base_path}/numbers", body=body if body else None, cast_to=PhoneNumber,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    def list(self, *, extra_headers: dict[str, str] | None = None, extra_query: dict[str, object] | None = None,
             timeout: float | None = None) -> list[NumberListItem]:
        """등록된 번호 목록을 조회합니다.

        페이지네이션과 필터가 없으며 계정이 보유한 번호가 한 번에 전부 반환됩니다.

        Args:
            extra_headers: 추가 HTTP 헤더.
            extra_query: 추가 쿼리 파라미터.
            timeout: 이 요청의 타임아웃 (초).

        Returns:
            NumberListItem 리스트.
        """
        from .._models import BaseModel

        class _NumbersResponse(BaseModel):
            data: list[NumberListItem]

        result = self._client._get(
            f"{self._base_path}/numbers", cast_to=_NumbersResponse,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )
        return result.data

    def update(self, number: str, *, routing_type: RoutingType | None = None,
               agent_id: str | None = None,
               call_flow_id: str | None = None,
               forward_to: str | None = None,
               sip_endpoint_id: str | None = None,
               sip_credential_id: str | None = None,
               webhook_url: str | None = None,
               webhook_method: Literal["POST", "GET"] | None = None,
               webhook_headers: dict[str, str] | None = None,
               call_context_url: str | None = None,
               status_callback: str | None = None,
               status_callback_events: str | None = None,
               dictionary_id: str | None = None,
               extra_headers: dict[str, str] | None = None, extra_query: dict[str, object] | None = None,
               timeout: float | None = None) -> NumberUpdateResponse:
        """등록된 번호의 설정을 수정합니다.

        착신 라우팅(webhook/agent/callflow/forward/sip/softphone)과 webhook, 상태 통지,
        받아쓰기 사전을 변경할 수 있습니다. 보낸 필드만 반영되고 생략한 필드는 유지됩니다.

        라우팅을 바꾸면 다른 라우팅 필드는 서버에서 자동으로 비워집니다. 예를 들어
        'agent' 에서 'webhook' 으로 되돌리면 agent_id 가 null 이 되므로, 다시 'agent' 로
        돌아갈 때 agent_id 를 새로 지정해야 합니다.

        Args:
            number: 수정할 전화번호.
            routing_type: 착신 라우팅 모드.
            agent_id: routing_type='agent' 일 때 필수. 같은 계정의 에이전트 id.
            call_flow_id: routing_type='callflow' 일 때 필수. 같은 계정의 콜 플로우 id.
            forward_to: routing_type='forward' 일 때 필수. 같은 계정이 보유한 번호.
            sip_endpoint_id: routing_type='sip' 일 때 필수. SipEndpoint id.
            sip_credential_id: routing_type='softphone' 일 때 필수. 등록 단말의 credential id.
            webhook_url: Webhook URL.
            webhook_method: 'POST' 또는 'GET'.
            webhook_headers: Webhook 호출 시 덧붙일 헤더.
            call_context_url: routing_type='agent' 에서 통화별 컨텍스트를 조회할 endpoint.
            status_callback: 수신 통화 상태 통지 URL.
            status_callback_events: 구독할 상태 이벤트(공백 구분).
            dictionary_id: 이 번호의 통화 전사에 적용할 받아쓰기 사전 id.
            extra_headers: 추가 HTTP 헤더.
            extra_query: 추가 쿼리 파라미터.
            timeout: 이 요청의 타임아웃 (초).

        Returns:
            수정된 NumberUpdateResponse 객체.

        Raises:
            BadRequestError: 수정할 필드 없음 / 라우팅에 필요한 id 누락 / 대상 소유권 없음.
            NotFoundError: 번호를 찾을 수 없음.
            PermissionDeniedError: 번호 소유권 없음 또는 sip_trunk 부가서비스 비활성.
            ConflictError: 엔드포인트·단말 비활성 또는 활성 라우트 없음.
        """
        body = _update_body(webhook_url, webhook_method, webhook_headers, call_context_url,
                            routing_type, agent_id, call_flow_id, forward_to,
                            sip_endpoint_id, sip_credential_id,
                            status_callback, status_callback_events, dictionary_id)
        return self._client._put(
            f"{self._base_path}/numbers/{number}", body=body, cast_to=NumberUpdateResponse,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    def delete(self, number: str, *, extra_headers: dict[str, str] | None = None,
               timeout: float | None = None) -> None:
        """등록된 번호를 삭제합니다. 번호는 풀로 복귀됩니다.

        되돌릴 수 없으며 같은 번호를 다시 발급받는다는 보장이 없습니다.

        Args:
            number: 삭제할 전화번호.
            extra_headers: 추가 HTTP 헤더.
            timeout: 이 요청의 타임아웃 (초).

        Raises:
            NotFoundError: 번호를 찾을 수 없음.
            PermissionDeniedError: 번호 소유권 없음.
            ServiceUnavailableError: 번호 반납 기능 사용 불가.
        """
        self._client._delete(f"{self._base_path}/numbers/{number}", extra_headers=extra_headers, timeout=timeout)


class AsyncNumbers(AsyncAPIResource):
    """전화번호(Numbers) 비동기 리소스."""

    async def create(self, *, webhook_url: str | None = None,
                     webhook_method: Literal["POST", "GET"] | None = None,
                     webhook_headers: dict[str, str] | None = None,
                     status_callback: str | None = None,
                     status_callback_events: str | None = None,
                     extra_headers: dict[str, str] | None = None,
                     extra_query: dict[str, object] | None = None, timeout: float | None = None) -> PhoneNumber:
        """번호를 비동기로 발급합니다."""
        body = _create_body(webhook_url, webhook_method, webhook_headers,
                            status_callback, status_callback_events)
        return await self._client._post(
            f"{self._base_path}/numbers", body=body if body else None, cast_to=PhoneNumber,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    async def list(self, *, extra_headers: dict[str, str] | None = None, extra_query: dict[str, object] | None = None,
                   timeout: float | None = None) -> list[NumberListItem]:
        """번호 목록을 비동기로 조회합니다."""
        from .._models import BaseModel

        class _NumbersResponse(BaseModel):
            data: list[NumberListItem]

        result = await self._client._get(
            f"{self._base_path}/numbers", cast_to=_NumbersResponse,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )
        return result.data

    async def update(self, number: str, *, routing_type: RoutingType | None = None,
                     agent_id: str | None = None,
                     call_flow_id: str | None = None,
                     forward_to: str | None = None,
                     sip_endpoint_id: str | None = None,
                     sip_credential_id: str | None = None,
                     webhook_url: str | None = None,
                     webhook_method: Literal["POST", "GET"] | None = None,
                     webhook_headers: dict[str, str] | None = None,
                     call_context_url: str | None = None,
                     status_callback: str | None = None,
                     status_callback_events: str | None = None,
                     dictionary_id: str | None = None,
                     extra_headers: dict[str, str] | None = None, extra_query: dict[str, object] | None = None,
                     timeout: float | None = None) -> NumberUpdateResponse:
        """번호 설정(착신 라우팅 + webhook + 상태 통지)을 비동기로 수정합니다."""
        body = _update_body(webhook_url, webhook_method, webhook_headers, call_context_url,
                            routing_type, agent_id, call_flow_id, forward_to,
                            sip_endpoint_id, sip_credential_id,
                            status_callback, status_callback_events, dictionary_id)
        return await self._client._put(
            f"{self._base_path}/numbers/{number}", body=body, cast_to=NumberUpdateResponse,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )

    async def delete(self, number: str, *, extra_headers: dict[str, str] | None = None,
                     timeout: float | None = None) -> None:
        """번호를 비동기로 삭제합니다."""
        await self._client._delete(f"{self._base_path}/numbers/{number}", extra_headers=extra_headers, timeout=timeout)
