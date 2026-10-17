"""수신 통화 선별(``@agent.on_incoming``) — 받기 전에 거절할 기회.

서버가 ``call.incoming`` 을 보내면 SDK 는 지금까지 무조건 ``call.accept`` 로 답했다. 훅을
등록하면 그 사이에 훅이 돌고, 훅이 ``reject()`` 를 부르면 ``call.reject`` 로 답한다.
서버는 사유가 있는 거절을 받으면 **응답하기 전에** 통화를 끝낸다(과금 없음, 다른 경로로
넘기지 않음). 사유 없는 거절은 서버가 다른 경로로 넘기므로 SDK 는 언제나 사유를 싣는다.

``IncomingCall`` 은 ``CallSession`` 이 **아니다**. 세션은 수락한 뒤에야 만들어진다 — 거절한
통화에는 세션도, 미디어도, ``session_setup`` 도 없다.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any, Literal

log = logging.getLogger("clawops.agent")

RejectReason = Literal["rejected", "busy"]
"""``"rejected"`` = SIP 403(기본), ``"busy"`` = SIP 486."""

REJECT_REASONS: tuple[str, ...] = ("rejected", "busy")

# 훅이 결정을 내려야 하는 상한(초). 서버는 응답을 11초 기다린다 — 여유를 넉넉히 남긴다.
# 이 시간을 넘기거나 훅이 예외를 던지면 수락한다(fail-open): 선별 코드의 결함이 통화를
# 통째로 막아서는 안 된다.
INCOMING_HOOK_DEADLINE_S = 5.0


class _Done:
    """이미 끝난 awaitable. ``reject()`` 가 동기 훅에서도 효과를 내게 하는 장치다.

    ``async def reject`` 였다면 동기 훅의 ``call.reject()`` 는 코루틴만 만들고 아무 일도 하지
    않는다. 결정은 호출 시점에 즉시 기록하고, ``await`` 는 형식으로만 받는다.
    """

    __slots__ = ()

    def __await__(self) -> Generator[Any, None, None]:
        return
        yield  # pragma: no cover — 제너레이터로 만들기 위한 자리


class IncomingCall:
    """받기 전의 수신 통화. ``@agent.on_incoming`` 훅의 인자다.

    Attributes:
        call_id: 통화 ID.
        from_number: 발신 번호. 번호 표시제한 통화도 번호는 그대로 온다.
        to_number: 이 에이전트의 번호(수신 번호).
        caller_id_restricted: 발신자가 번호 표시제한을 걸고 건 통화면 ``True``.
        forwarded_from: 착신전환으로 들어온 통화에서 발신자가 원래 건 번호. 착신전환을 실행한
            통신사가 전환 정보를 함께 보내온 경우에만 값이 있고, 직접 걸려온 통화는 ``None``.
    """

    __slots__ = (
        "_closed",
        "_on_decide",
        "_reason",
        "call_id",
        "caller_id_restricted",
        "forwarded_from",
        "from_number",
        "to_number",
    )

    def __init__(
        self,
        *,
        call_id: str,
        from_number: str,
        to_number: str,
        caller_id_restricted: bool = False,
        forwarded_from: str | None = None,
    ) -> None:
        self.call_id = call_id
        self.from_number = from_number
        self.to_number = to_number
        self.caller_id_restricted = caller_id_restricted
        self.forwarded_from = forwarded_from
        self._reason: str | None = None
        # 결정 시한이 지났다 — 이미 수락(또는 거절)을 보냈으므로 더는 바꿀 수 없다.
        self._closed = False
        # 결정 즉시 에이전트를 깨우는 콜백(훅이 끝나기를 기다리지 않는다).
        self._on_decide: Any = None

    @property
    def rejected(self) -> bool:
        """``reject()`` 가 시한 안에 불렸는가."""
        return self._reason is not None

    @property
    def reject_reason(self) -> RejectReason | None:
        return self._reason  # type: ignore[return-value]

    def reject(self, reason: RejectReason = "rejected") -> _Done:
        """이 통화를 받지 않고 거절한다. ``await call.reject()`` 로 부른다.

        Args:
            reason: ``"rejected"``(기본, SIP 403) 또는 ``"busy"``(SIP 486, 통화중).

        두 번 불러도 첫 결정이 유지된다. 결정 시한(5초)이 지난 뒤에 부르면 이미 수락한
        통화라 아무 일도 하지 않고 경고만 남긴다.
        """
        if reason not in REJECT_REASONS:
            raise ValueError(f"reason={reason!r} — 'rejected' 또는 'busy' 만 됩니다")
        if self._closed:
            log.warning(
                "IncomingCall.reject() — 결정 시한이 지나 이미 처리된 통화입니다(무시): %s",
                self.call_id,
            )
            return _Done()
        if self._reason is not None:
            return _Done()
        self._reason = reason
        if self._on_decide is not None:
            self._on_decide()
        return _Done()

    def __repr__(self) -> str:
        return (
            f"IncomingCall(call_id={self.call_id!r}, from_number={self.from_number!r}, "
            f"to_number={self.to_number!r}, caller_id_restricted={self.caller_id_restricted!r}, "
            f"forwarded_from={self.forwarded_from!r})"
        )
