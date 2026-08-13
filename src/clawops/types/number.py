from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from .._models import BaseModel

RoutingType = Literal["webhook", "sip", "softphone", "forward", "agent", "callflow"]
"""착신 라우팅 모드.

- ``webhook``   : webhook_url 의 VoiceML 이 처리.
- ``agent``     : agent_id 의 매니지드 에이전트가 착신.
- ``callflow``  : call_flow_id 의 콜 플로우(ARS)가 착신.
- ``forward``   : forward_to(같은 계정 보유 번호)로 내부 착신전환.
- ``sip``       : sip_endpoint_id 의 라우트로 외부 PBX 다이얼.
- ``softphone`` : sip_credential_id 의 등록 단말로 착신.
"""


class PhoneNumber(BaseModel):
    """전화번호 모델.

    Attributes:
        number: 전화번호. 이 값이 곧 식별자다.
        number_type: 번호 유형 ('did'=일반, 'representative'=대표번호).
        source: 번호 출처 (풀 발급은 'pool').
        routing_type: 착신 라우팅 모드. 값 목록은 RoutingType 참고.
        agent_id: routing_type='agent' 일 때 착신을 처리할 에이전트 id.
        call_flow_id: routing_type='callflow' 일 때 착신을 처리할 콜 플로우 id.
        forward_to: routing_type='forward' 일 때 전환할 대상 번호.
        sip_endpoint_id: routing_type='sip' 일 때 라우팅할 SipEndpoint id.
        sip_credential_id: routing_type='softphone' 일 때 착신할 SIP credential(단말) id.
        webhook_url: Webhook URL. 미설정 시 None.
        webhook_method: Webhook HTTP 메서드.
        webhook_headers: Webhook 호출 시 덧붙일 헤더.
        call_context_url: routing_type='agent' 에서 통화별 컨텍스트를 조회할 endpoint.
        status_callback: 수신(inbound) 통화 상태 통지 URL.
        status_callback_events: 구독할 상태 이벤트(공백 구분).
        dictionary_id: 이 번호의 통화 전사에 적용할 받아쓰기 사전 id.
        created_at: 등록 시각.
    """

    number: str
    number_type: Optional[str] = None
    source: Optional[str] = None
    # 응답의 routing_type 은 Literal 로 좁히지 않는다. 좁히면 서버가 라우팅 종류를 늘렸을 때
    # 그 번호가 섞인 목록 조회가 통째로 ValidationError 로 실패한다(0.40.0 까지의 실제 결함:
    # 'agent' 로 라우팅된 번호 하나가 numbers.list() 전체를 깨뜨렸다).
    routing_type: Optional[str] = None
    agent_id: Optional[str] = None
    call_flow_id: Optional[str] = None
    forward_to: Optional[str] = None
    sip_endpoint_id: Optional[str] = None
    sip_credential_id: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_method: Optional[str] = None
    webhook_headers: Optional[dict[str, str]] = None
    call_context_url: Optional[str] = None
    status_callback: Optional[str] = None
    status_callback_events: Optional[str] = None
    dictionary_id: Optional[str] = None
    created_at: Optional[datetime] = None


NumberListItem = PhoneNumber
NumberUpdateResponse = PhoneNumber
