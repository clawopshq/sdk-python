from datetime import datetime

from clawops.types.call import Call, CallControlResponse
from clawops.types.number import PhoneNumber, NumberListItem
from clawops.types.shared import PaginationMeta


def test_call_from_api_response():
    data = {
        "callId": "CAabcdef1234567890abcdef1234567890",
        "status": "queued",
        "to": "01012345678",
        "from": "07052358010",
        "direction": "outbound",
        "duration": None,
        "accountId": "AC1a2b3c4d",
        "dateCreated": "2025-06-01T12:00:00Z",
        "dateUpdated": None,
    }
    call = Call.model_validate(data)
    assert call.call_id == "CAabcdef1234567890abcdef1234567890"
    assert call.status == "queued"
    assert call.to == "01012345678"
    assert call.from_ == "07052358010"
    assert call.direction == "outbound"
    assert call.duration is None
    assert call.account_id == "AC1a2b3c4d"
    assert isinstance(call.date_created, datetime)
    assert call.date_updated is None


def test_call_hangup_cause_from_api_response():
    """실패 통화의 종료 사유 — 결번을 재시도 대상과 구분하는 값."""
    data = {
        "callId": "CA14ad61795d28ba036a383f66565376b4",
        "status": "failed",
        "to": "07080588491",
        "from": "07052361088",
        "direction": "outbound",
        "duration": 0,
        "accountId": "AC1a2b3c4d",
        "dateCreated": "2026-07-30T17:18:15Z",
        "dateUpdated": "2026-07-30T17:18:15Z",
        "hangupCause": "invalid_number",
        "hangupCauseQ850": 1,
        "sipResponseCode": 404,
        "hangupSource": "carrier",
    }
    call = Call.model_validate(data)
    assert call.hangup_cause == "invalid_number"
    assert call.hangup_cause_q850 == 1
    assert call.sip_response_code == 404
    assert call.hangup_source == "carrier"


def test_call_hangup_cause_absent_is_none():
    """종료 전이거나 사유 미상인 통화는 네 필드 모두 None (하위호환)."""
    data = {
        "callId": "CA123",
        "status": "in-progress",
        "to": "01012345678",
        "from": "07052358010",
        "direction": "outbound",
        "accountId": "AC1a2b3c4d",
        "dateCreated": "2026-07-30T17:18:15Z",
    }
    call = Call.model_validate(data)
    assert call.hangup_cause is None
    assert call.hangup_cause_q850 is None
    assert call.sip_response_code is None
    assert call.hangup_source is None


def test_call_unknown_hangup_cause_is_accepted():
    """서버가 새 cause 를 보내도 파싱은 깨지지 않는다 (Literal 로 좁히지 않은 이유)."""
    data = {
        "callId": "CA123",
        "status": "failed",
        "to": "01012345678",
        "from": "07052358010",
        "direction": "outbound",
        "accountId": "AC1a2b3c4d",
        "dateCreated": "2026-07-30T17:18:15Z",
        "hangupCause": "some_future_cause",
        "hangupCauseQ850": 255,
    }
    call = Call.model_validate(data)
    assert call.hangup_cause == "some_future_cause"
    assert call.hangup_cause_q850 == 255


def test_call_control_response():
    data = {"callId": "CA123", "status": "completed"}
    resp = CallControlResponse.model_validate(data)
    assert resp.call_id == "CA123"
    assert resp.status == "completed"


def test_phone_number_from_api():
    data = {"number": "07012340001"}
    num = PhoneNumber.model_validate(data)
    assert num.number == "07012340001"


def test_number_list_item():
    data = {
        "number": "07012340001",
        "webhookUrl": "https://my-app.com/voice",
        "createdAt": "2025-06-01T12:00:00Z",
    }
    item = NumberListItem.model_validate(data)
    assert item.number == "07012340001"
    assert item.webhook_url == "https://my-app.com/voice"


def test_pagination_meta():
    data = {"total": 100, "page": 2, "pageSize": 20}
    meta = PaginationMeta.model_validate(data)
    assert meta.total == 100
    assert meta.page == 2
    assert meta.page_size == 20
