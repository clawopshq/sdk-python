from __future__ import annotations

from typing import Any, Literal, Optional, Union

import httpx

# 서버는 실패 응답을 `{"error": "...", "code": "..."}` 로 준다. code 없이 읽으면
# 수신거부(422 recipient_blocked)와 할당량 초과(422 quota_exceeded)를 **한글 메시지로**
# 구분해야 한다.
#
# ⚠️ 서버가 도메인마다 표기를 달리 쓴다 — 문자 도메인은 snake_case, 채널 도메인은
# SCREAMING_CASE 다. SDK 가 임의로 정규화하면 실제 응답과 어긋나므로 **섞인 채로** 옮긴다.
# 열린 유니온이라 여기 없는 코드도 그대로 들어온다(자동완성만 돕는다).
ClawOpsErrorCode = Union[
    Literal[
        # ── 문자·알림톡 발송 (services/messages.ts · kakao-send.ts) ──
        "kakao_required",
        "kakao_type_conflict",
        "kakao_body_not_allowed",
        "kakao_subject_not_allowed",
        "kakao_media_not_allowed",
        "kakao_unavailable",
        "kakao_send_failed",
        "kakao_variable_missing",
        "kakao_variable_unknown",
        "kakao_channel_not_found",
        "kakao_template_not_found",
        "kakao_template_not_approved",
        "kakao_template_dormant",
        # 브랜드 메시지 — 알림톡과 공유하는 코드(kakao_body_not_allowed 등)는 위에 있다.
        "kakao_brand_required",
        "kakao_brand_template_not_found",
        # 광고성이라 20:50~08:00(KST)에는 접수되지 않는다. 하루 11시간 동안 나오므로
        # 재시도 스케줄링이 이 분기에 달린다 — 오타 한 글자가 그 로직을 무력화한다.
        "kakao_brand_night_blocked",
        # 브랜드는 대체발송이 없다 — fallback 을 실으면 이 코드다.
        "kakao_fallback_not_allowed",
        "body_too_long",
        "invalid_phone",
        "invalid_input",
        "from_not_registered",
        "sms_no_media",
        "sms_no_subject",
        "media_download_failed",
        "recipient_blocked",
        "messaging_blocked",
        "quota_exceeded",
        "override_quota_exceeded",
        "no_active_subscription",
        "not_found",
        # ── 카카오 채널 연동 (services/kakao-channels.ts) ──
        "KAKAO_TOKEN_INVALID",
        "KAKAO_CHANNEL_ALREADY_LINKED",
        "KAKAO_CHANNEL_REJECTED",
        "KAKAO_RATE_LIMITED",
        "KAKAO_PROVIDER_UNAVAILABLE",
        "VALIDATION",
    ],
    str,
]
"""실패 응답의 ``code``. 사유를 한글 문구가 아니라 값으로 분기하기 위한 것이다."""


def _extract_error_code(body: Any) -> Optional[ClawOpsErrorCode]:
    """응답 body 에서 ``code`` 를 꺼낸다. 없거나 문자열이 아니면 None."""
    if isinstance(body, dict):
        code = body.get("code")
        if isinstance(code, str) and code:
            return code
    return None


class ClawOpsError(Exception):
    """ClawOps SDK의 모든 에러의 베이스 클래스."""


class APIError(ClawOpsError):
    """API 호출 관련 에러의 베이스 클래스."""

    message: str
    request: httpx.Request

    def __init__(self, message: str, *, request: httpx.Request) -> None:
        super().__init__(message)
        self.message = message
        self.request = request


class APIStatusError(APIError):
    """HTTP 상태 코드 에러 (4xx/5xx)."""

    status_code: int
    response: httpx.Response
    body: Any | None
    code: ClawOpsErrorCode | None
    """실패 사유 코드. 서버가 주지 않았으면 ``None``.

    같은 상태 코드에 여러 사유가 몰리므로(422 만 해도 수신거부·할당량 초과·템플릿
    미승인이 섞인다) 분기는 이 값으로 합니다. 한글 메시지는 문구가 바뀝니다.

        try:
            client.messages.create(..., kakao={...})
        except BadRequestError as e:
            if e.code == "kakao_variable_missing":
                ...
    """

    def __init__(
        self,
        message: str,
        *,
        response: httpx.Response,
        body: Any | None,
    ) -> None:
        super().__init__(message, request=response.request)
        self.status_code = response.status_code
        self.response = response
        self.body = body
        self.code = _extract_error_code(body)


class BadRequestError(APIStatusError):
    """HTTP 400 Bad Request."""
    status_code: int = 400


class AuthenticationError(APIStatusError):
    """HTTP 401 Unauthorized."""
    status_code: int = 401


class PermissionDeniedError(APIStatusError):
    """HTTP 403 Forbidden."""
    status_code: int = 403


class NotFoundError(APIStatusError):
    """HTTP 404 Not Found."""
    status_code: int = 404


class ConflictError(APIStatusError):
    """HTTP 409 Conflict."""
    status_code: int = 409


class UnprocessableEntityError(APIStatusError):
    """HTTP 422 Unprocessable Entity."""
    status_code: int = 422


class RateLimitError(APIStatusError):
    """HTTP 429 Too Many Requests.

    동시 통화 한도 초과 등 일시적 제한. SDK는 자동 재시도(최대 2회, 지수 backoff)를 수행한다.
    즉각 피드백이 필요하면 client 생성 시 max_retries=0으로 재시도를 비활성화하라.
    """
    status_code: int = 429


class InternalServerError(APIStatusError):
    """HTTP 500+ Internal Server Error."""
    status_code: int = 500


class ServiceUnavailableError(APIStatusError):
    """HTTP 503 Service Unavailable."""
    status_code: int = 503


class APIConnectionError(APIError):
    """네트워크 연결 실패."""

    def __init__(self, *, message: str = "Connection error.", request: httpx.Request) -> None:
        super().__init__(message, request=request)


class APITimeoutError(APIConnectionError):
    """요청 타임아웃."""

    def __init__(self, *, request: httpx.Request) -> None:
        super().__init__(message="Request timed out.", request=request)


class APIResponseValidationError(APIError):
    """API 응답이 예상된 스키마와 일치하지 않습니다."""

    status_code: int
    response: httpx.Response

    def __init__(
        self,
        *,
        response: httpx.Response,
        message: str = "API response validation failed.",
    ) -> None:
        super().__init__(message, request=response.request)
        self.status_code = response.status_code
        self.response = response


_STATUS_CODE_TO_ERROR: dict[int, type[APIStatusError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    422: UnprocessableEntityError,
    429: RateLimitError,
    500: InternalServerError,
    503: ServiceUnavailableError,
}


def _make_status_error(*, response: httpx.Response) -> APIStatusError:
    """HTTP 응답 상태 코드를 적절한 예외 클래스로 매핑합니다."""
    try:
        body = response.json()
    except Exception:
        body = None

    message = ""
    if isinstance(body, dict) and "error" in body:
        message = body["error"]
    else:
        message = f"HTTP {response.status_code}"

    err_cls = _STATUS_CODE_TO_ERROR.get(response.status_code)
    if err_cls is None:
        if response.status_code >= 500:
            err_cls = InternalServerError
        else:
            err_cls = APIStatusError

    return err_cls(message=message, response=response, body=body)


class AgentError(ClawOpsError):
    """Agent 관련 에러의 베이스 클래스."""


class AgentConnectionError(AgentError):
    """Agent WebSocket 연결 실패."""
