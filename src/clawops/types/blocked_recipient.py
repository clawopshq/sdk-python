from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from .._models import BaseModel

#: 차단 채널. ``call``=전화, ``message``=문자(SMS/LMS/MMS 공통), ``email``=이메일.
#:
#: .. warning::
#:    **요청을 쓸 때의 도움말**이고, 응답의 ``channel`` 은 ``str`` 입니다. 응답 타입을
#:    ``Literal`` 로 좁혀 두면 서버가 채널을 늘리는 날 옛 SDK 가 파싱 단계에서 통째로
#:    실패합니다 — 실제로 그 일이 있었습니다(``email`` 추가 시점).
BlockedChannel = Literal["call", "message", "email"]

BlockedRecipientStatus = Literal["active", "released", "all"]

#: 접수 경로. 공개 API 로 등록하면 이 넷 중 하나입니다.
#:
#: .. warning::
#:    응답에는 **여기 없는 값도 옵니다** — ``ars``(ARS 수신거부 9번)·``sms``(문자 회신)
#:    처럼 내부 접수 경로로 들어온 항목이 그렇습니다. 그래서 응답의 ``source`` 는 ``str``
#:    입니다.
BlockedRecipientSource = Literal["api", "console", "import", "agent"]


class BlockedRecipient(BaseModel):
    """수신거부(DNC) 항목.

    등록된 대상은 해당 계정의 **발신**(전화·문자·이메일)에서 제외됩니다. 착신은 막지 않습니다.
    같은 상대라도 채널(call/message/email)마다 별개 항목입니다.

    .. warning::
       **채널마다 막는 방식이 다릅니다.** 전화·문자는 대상이 명단에 있으면 그 발신 요청
       **전체**가 ``422 recipient_blocked`` 로 거절되지만, 이메일은 차단된 수신자만 **빼고
       나머지에게는 보냅니다**. 응답의 ``suppressed`` 에 빠진 주소가 담기므로 ``2xx`` 를
       받아도 그 칸을 확인해야 누가 안 갔는지 알 수 있습니다. 수신자가 전원 걸렸을 때만
       ``422`` 입니다.

    Attributes:
        id: 항목 ID.
        recipient: 수신거부한 상대. **채널과 무관하게 항상 이 칸**입니다 — call/message 면
            국내 표기로 정규화된 전화번호(예 '01012345678'), email 이면 소문자로 정규화된
            이메일 주소(예 'kim@example.com').
        channel: 'call'(전화) · 'message'(문자 — SMS/LMS/MMS 공통) · 'email'(이메일).
            ⚠️ ``str`` 인 것은 의도입니다 — 서버가 채널을 늘려도 파싱이 깨지지 않게.
        active: 지금 차단 중인지 여부. 해제된 항목도 이력으로 남아 조회되므로 이 값으로 구분합니다.
        source: 접수 경로. 공개 API 등록은 api/console/import/agent, 내부 접수는 ars/sms.
        source_ref: 증빙 링크(통화 id 또는 메시지 id). 가리키는 대상은 source 가 결정합니다.
        note: 자유 메모.
        created_by: 등록 주체. 자동 접수는 None.
        created_at: 수신거부 접수 시각.
        updated_at: 마지막 변경 시각.
        unblocked_at: 해제 시각. None 이면 차단 중.
        unblocked_source: 해제 경로.
        unblocked_by: 해제 주체. 자동 해제는 None.
        unblocked_note: 해제 사유 메모.
    """

    id: str
    recipient: str
    # ⛔ Literal 로 좁히지 않는다 — 서버가 채널을 늘리면 옛 SDK 가 전부 깨진다.
    channel: str
    active: bool
    source: str
    source_ref: Optional[str] = None
    note: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    unblocked_at: Optional[datetime] = None
    unblocked_source: Optional[str] = None
    unblocked_by: Optional[str] = None
    unblocked_note: Optional[str] = None
