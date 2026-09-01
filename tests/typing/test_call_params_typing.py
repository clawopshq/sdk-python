"""타입 수준 회귀 — 이 디렉터리의 검사 기법 자체가 살아 있는지 고정한다."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clawops.types.call_params import CallCreateParams

    # `to` 는 Required 다. 빠지면 타입 에러여야 한다.
    _missing_required: CallCreateParams = {  # type: ignore[typeddict-item]
        "from_": "07052358010",
        "url": "https://example.com/twiml",
    }

    # TypedDict 는 키 집합이 닫혀 있다. 없는 키는 타입 에러여야 한다.
    _unknown_key: CallCreateParams = {
        "to": "01012345678",
        "from_": "07052358010",
        "nope": 1,  # type: ignore[typeddict-unknown-key]
    }
