from __future__ import annotations

from typing import Annotated, Literal, Optional

from typing_extensions import TypedDict

from .._utils import PropertyInfo
from .number import RoutingType


class NumberCreateParams(TypedDict, total=False):
    """번호 발급 요청 파라미터."""

    webhook_url: Annotated[Optional[str], PropertyInfo(alias="webhookUrl")]
    """수신 전화 처리용 Webhook URL."""

    webhook_method: Annotated[Literal["POST", "GET"], PropertyInfo(alias="webhookMethod")]
    """Webhook 호출 HTTP 메서드."""

    webhook_headers: Annotated[Optional[dict[str, str]], PropertyInfo(alias="webhookHeaders")]
    """Webhook 호출 시 덧붙일 헤더. 키는 'X-' 로 시작해야 한다."""

    status_callback: Annotated[Optional[str], PropertyInfo(alias="statusCallback")]
    """수신(inbound) 통화 상태 통지 URL."""

    status_callback_events: Annotated[Optional[str], PropertyInfo(alias="statusCallbackEvents")]
    """구독할 상태 이벤트(공백 구분). 미지정 시 'initiated ringing answered completed'."""


class NumberUpdateParams(TypedDict, total=False):
    """번호 설정 수정 요청 파라미터."""

    routing_type: Annotated[RoutingType, PropertyInfo(alias="routingType")]
    """착신 라우팅 모드."""

    agent_id: Annotated[Optional[str], PropertyInfo(alias="agentId")]
    """routing_type='agent' 일 때 필수. 같은 계정의 에이전트 id."""

    call_flow_id: Annotated[Optional[str], PropertyInfo(alias="callFlowId")]
    """routing_type='callflow' 일 때 필수. 같은 계정의 콜 플로우 id."""

    forward_to: Annotated[Optional[str], PropertyInfo(alias="forwardTo")]
    """routing_type='forward' 일 때 필수. 같은 계정이 보유한 번호."""

    sip_endpoint_id: Annotated[Optional[str], PropertyInfo(alias="sipEndpointId")]
    """routing_type='sip' 일 때 필수. SipEndpoint id."""

    sip_credential_id: Annotated[Optional[str], PropertyInfo(alias="sipCredentialId")]
    """routing_type='softphone' 일 때 필수. 등록 단말의 SIP credential id."""

    webhook_url: Annotated[Optional[str], PropertyInfo(alias="webhookUrl")]
    """수신 전화 처리용 Webhook URL."""

    webhook_method: Annotated[Literal["POST", "GET"], PropertyInfo(alias="webhookMethod")]
    """Webhook 호출 HTTP 메서드."""

    webhook_headers: Annotated[Optional[dict[str, str]], PropertyInfo(alias="webhookHeaders")]
    """Webhook 호출 시 덧붙일 헤더."""

    call_context_url: Annotated[Optional[str], PropertyInfo(alias="callContextUrl")]
    """routing_type='agent' 에서 통화별 컨텍스트를 조회할 endpoint."""

    status_callback: Annotated[Optional[str], PropertyInfo(alias="statusCallback")]
    """수신(inbound) 통화 상태 통지 URL."""

    status_callback_events: Annotated[Optional[str], PropertyInfo(alias="statusCallbackEvents")]
    """구독할 상태 이벤트(공백 구분)."""

    dictionary_id: Annotated[Optional[str], PropertyInfo(alias="dictionaryId")]
    """이 번호의 통화 전사에 적용할 받아쓰기 사전 id."""
