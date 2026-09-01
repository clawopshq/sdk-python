import httpx
import pytest

from clawops._exceptions import (
    ClawOpsError,
    APIError,
    APIStatusError,
    APIConnectionError,
    APITimeoutError,
    APIResponseValidationError,
    BadRequestError,
    AuthenticationError,
    PermissionDeniedError,
    NotFoundError,
    ConflictError,
    UnprocessableEntityError,
    InternalServerError,
    ServiceUnavailableError,
    _make_status_error,
)


def test_error_hierarchy():
    assert issubclass(APIError, ClawOpsError)
    assert issubclass(APIStatusError, APIError)
    assert issubclass(APIConnectionError, APIError)
    assert issubclass(APITimeoutError, APIConnectionError)
    assert issubclass(APIResponseValidationError, APIError)
    assert issubclass(BadRequestError, APIStatusError)
    assert issubclass(AuthenticationError, APIStatusError)
    assert issubclass(PermissionDeniedError, APIStatusError)
    assert issubclass(NotFoundError, APIStatusError)
    assert issubclass(ConflictError, APIStatusError)
    assert issubclass(UnprocessableEntityError, APIStatusError)
    assert issubclass(InternalServerError, APIStatusError)
    assert issubclass(ServiceUnavailableError, APIStatusError)


def test_api_status_error_attributes():
    request = httpx.Request("GET", "https://api.claw-ops.com/v1/accounts/AC123/calls")
    response = httpx.Response(404, json={"error": "not found"}, request=request)
    err = NotFoundError(message="not found", response=response, body={"error": "not found"})
    assert err.status_code == 404
    assert err.body == {"error": "not found"}
    assert err.response is response
    assert err.request is request
    assert "not found" in str(err)


def test_make_status_error_mapping():
    request = httpx.Request("GET", "https://api.claw-ops.com/test")
    cases = [
        (400, BadRequestError),
        (401, AuthenticationError),
        (403, PermissionDeniedError),
        (404, NotFoundError),
        (409, ConflictError),
        (422, UnprocessableEntityError),
        (500, InternalServerError),
        (502, InternalServerError),
        (503, ServiceUnavailableError),
    ]
    for status_code, expected_cls in cases:
        response = httpx.Response(status_code, json={"error": "test"}, request=request)
        err = _make_status_error(response=response)
        assert isinstance(err, expected_cls), f"Expected {expected_cls} for {status_code}"


def test_api_connection_error():
    request = httpx.Request("GET", "https://api.claw-ops.com/test")
    err = APIConnectionError(message="Connection refused", request=request)
    assert "Connection refused" in str(err)


def test_api_timeout_error():
    request = httpx.Request("GET", "https://api.claw-ops.com/test")
    err = APITimeoutError(request=request)
    assert isinstance(err, APIConnectionError)


class TestErrorCode:
    """실패 사유를 한글 문구가 아니라 code 로 분기할 수 있어야 한다.

    같은 상태 코드에 여러 사유가 몰린다 — 422 만 해도 수신거부·할당량 초과·템플릿
    미승인이 섞인다. 서버는 `{error, code}` 로 주는데 SDK 가 code 를 읽지 않아
    사용자가 메시지 문자열을 비교해야 했다.
    """

    request = httpx.Request("POST", "https://api.claw-ops.com/test")

    def _err(self, status, payload):
        return _make_status_error(response=httpx.Response(status, json=payload, request=self.request))

    def test_code_is_extracted(self):
        err = self._err(400, {"error": "템플릿 변수가 누락되었습니다", "code": "kakao_variable_missing"})
        assert err.code == "kakao_variable_missing"
        assert err.message == "템플릿 변수가 누락되었습니다"

    def test_screaming_case_is_kept_as_is(self):
        """채널 도메인은 SCREAMING_CASE 다. SDK 가 정규화하면 실제 응답과 어긋난다."""
        assert self._err(422, {"error": "x", "code": "KAKAO_TOKEN_INVALID"}).code == "KAKAO_TOKEN_INVALID"

    def test_same_status_different_codes(self):
        blocked = self._err(422, {"error": "수신거부", "code": "recipient_blocked"})
        quota = self._err(422, {"error": "한도 초과", "code": "quota_exceeded"})
        assert type(blocked) is type(quota)
        assert blocked.code != quota.code

    def test_unknown_code_passes_through(self):
        """열린 유니온이다 — 서버가 코드를 새로 만들어도 그대로 실린다."""
        assert self._err(400, {"error": "x", "code": "some_future_code"}).code == "some_future_code"

    def test_missing_code_is_none(self):
        assert self._err(404, {"error": "없음"}).code is None

    def test_non_json_body_is_none(self):
        err = _make_status_error(
            response=httpx.Response(502, text="<html>bad gateway</html>", request=self.request)
        )
        assert err.code is None
        assert err.body is None

    def test_empty_code_is_none(self):
        assert self._err(400, {"error": "x", "code": ""}).code is None

    def test_non_string_code_is_none(self):
        assert self._err(400, {"error": "x", "code": 42}).code is None
