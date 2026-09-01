"""타입 수준 회귀 — messages.create() 오버로드.

TypedDict 유니온(test_message_params_typing)은 **파라미터 딕셔너리**를 지킨다.
이 파일은 **실제 호출 시그니처**를 지킨다. 이 SDK 의 리소스 메서드는 TypedDict 가
아니라 키워드 인자를 받으므로, 배타 규칙이 오버로드로 표현돼 있고 그게 살아
있는지는 따로 확인해야 한다.

``if TYPE_CHECKING`` 아래라 런타임에는 아무것도 실행되지 않는다 — 호출문이
실제로 나가지 않는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clawops import AsyncClawOps, ClawOps
    from clawops.types.message_params import KakaoSendParam

    client: ClawOps
    aclient: AsyncClawOps
    KAKAO: KakaoSendParam

    # ── 통과해야 하는 것 ──────────────────────────────────────
    client.messages.create(to="010", from_="070", body="안녕하세요")
    client.messages.create(to="010", from_="070", body="긴 본문", type="lms", subject="제목")
    client.messages.create(to="010", from_="070", body="사진", type="mms", media_url=["https://e.com/a.jpg"])
    client.messages.create(to="010", from_="070", kakao=KAKAO)
    client.messages.create(to="010", from_="070", kakao=KAKAO, type="ata", fallback={"body": "대체"})
    client.messages.list(type="ata", number="07052358010")

    # ── 막아야 하는 것 ────────────────────────────────────────
    # 문자와 알림톡을 섞을 수 없다.
    client.messages.create(  # type: ignore[call-overload]
        to="010", from_="070", body="안녕하세요", kakao=KAKAO,
    )
    client.messages.create(  # type: ignore[call-overload]
        to="010", from_="070", kakao=KAKAO, media_url=["https://e.com/a.jpg"],
    )
    client.messages.create(  # type: ignore[call-overload]
        to="010", from_="070", kakao=KAKAO, subject="제목",
    )
    client.messages.create(  # type: ignore[call-overload]
        to="010", from_="070", kakao=KAKAO, type="sms",
    )
    # fallback 은 알림톡 전용이다.
    client.messages.create(  # type: ignore[call-overload]
        to="010", from_="070", body="안녕하세요", fallback={"body": "대체"},
    )
    # body 도 kakao 도 없으면 어느 오버로드에도 맞지 않는다.
    client.messages.create(to="010", from_="070")  # type: ignore[call-overload]
    # 응답에만 있는 상태는 필터로 못 쓴다.
    client.messages.list(status="sending")  # type: ignore[arg-type]

    # ── async 도 같은 규칙이다 ────────────────────────────────
    _coro = aclient.messages.create(to="010", from_="070", kakao=KAKAO)
    aclient.messages.create(  # type: ignore[call-overload]
        to="010", from_="070", body="안녕하세요", kakao=KAKAO,
    )
