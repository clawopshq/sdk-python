"""ClawOps Voice API의 공식 Python SDK.

Example::

    from clawops import ClawOps

    client = ClawOps(api_key="sk_...", account_id="AC1a2b3c4d")
    call = client.calls.create(
        to="01012345678", from_="07052358010",
        url="https://my-app.com/twiml",
    )
    print(call.call_id)
"""

from ._client import AsyncClawOps, ClawOps
from ._exceptions import (
    APIConnectionError,
    APIError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    ClawOpsError,
    ClawOpsErrorCode,
    ConflictError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
    UnprocessableEntityError,
)
from ._version import __version__
from .webhooks import WebhookVerificationError

__all__ = [
    "ClawOps",
    "AsyncClawOps",
    "ClawOpsError",
    "ClawOpsErrorCode",
    "APIError",
    "APIStatusError",
    "APIConnectionError",
    "APITimeoutError",
    "APIResponseValidationError",
    "BadRequestError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "ConflictError",
    "UnprocessableEntityError",
    "RateLimitError",
    "InternalServerError",
    "ServiceUnavailableError",
    "WebhookVerificationError",
    "__version__",
]

_locals = locals()
for _name in __all__:
    if not _name.startswith("__"):
        try:
            _locals[_name].__module__ = "clawops"
        except (TypeError, AttributeError):
            pass
