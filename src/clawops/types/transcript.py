from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional, Union

from .._models import BaseModel

# ⚠️ 아래 둘 다 닫힌 Literal 로 두지 않는다 — `MessageType` 과 같은 이유이고, 이 응답에서만
# 두 번 깨졌다. `segments` 는 리스트라 **조각 하나가 전사 응답 전체를 죽인다.**
#
# **2026-08 이후 전사는 서버가 화자를 `speaker_0`·`speaker_1`… 로 보낸다**(전환 통화처럼
# 참여자가 셋 이상이면 그만큼 늘어난다). 그 이전 전사에는 `AGENT`·`CUSTOMER` 가 그대로 남아
# 있으므로 **두 형식을 모두 받아야** 한다. 화자와 역할의 연결은 보장되지 않는다.
TranscriptSpeaker = Union[Literal["CUSTOMER", "AGENT"], str]
"""화자 식별자. 서버가 형식을 바꿀 수 있어 열려 있다."""

# ⛔ **실패 단계는 서버 코드가 만든다** — 전사 파이프라인이 `download`·`runtime`·`transcription`·
#    `recover` 를 내보내고, 영구 실패는 예외 객체의 속성(`exc.stage`)을 그대로 싣는다.
#    즉 어휘가 열려 있고, 서버 스펙의 enum 조차 그 스냅샷일 뿐이다.
#
# ⚠️ 여기가 닫혀 있으면 **전사가 실패했을 때 그 이유를 물으면 던진다** — 고객이 가장 답을
#    필요로 하는 순간이다.
TranscriptStage = Union[Literal["download", "runtime", "transcription", "trigger", "recover"], str]
"""실패 단계. `trigger` 는 시스템 레벨 실패라 재요청할 수 있다."""


class TranscriptSegment(BaseModel):
    """전사 segment 한 덩어리 — 화자 분리와 타임스탬프 포함."""

    speaker: TranscriptSpeaker
    start: float
    end: float
    text: str


class TranscriptStatus(BaseModel):
    """통화 전사 상태. 모든 필드는 status 에 따라 채워지는 것이 다름.

    - status="completed": call_id, segment_count, segments 채워짐
    - status="pending":   started_at 채워짐
    - status="failed":    stage, error 채워짐. stage="trigger" 는 시스템 레벨
                          실패로, POST 로 재시도 가능.
    - status="not_requested": 이외 필드 비어있음
    """

    status: Literal["completed", "pending", "failed", "not_requested"]
    call_id: Optional[str] = None
    segment_count: Optional[int] = None
    segments: Optional[List[TranscriptSegment]] = None
    started_at: Optional[datetime] = None
    stage: Optional[TranscriptStage] = None
    error: Optional[str] = None


class TranscriptRequestAccepted(BaseModel):
    """POST 요청이 accept 되어 Job 이 트리거된 상태 (202)."""

    status: Literal["pending"]
    call_id: str
