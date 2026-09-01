"""타입 수준 회귀 — 문자와 알림톡은 섞일 수 없다.

서버 규칙이 배타적이다. ``Kakao`` 를 실으면 ``Body``·``Subject``·``MediaUrl`` 은
금지이고 ``Type`` 은 ``ata`` 만 허용된다. 옵셔널 필드로 얹어 두면 이 규칙을
``400`` 으로 런타임에야 만나므로, TypedDict 유니온과 오버로드로 컴파일 시점에
막는다. 여기가 그 규칙이 살아 있는지 지키는 자리다.

TypeScript 와 달리 ``?: never`` 스탬프가 필요 없다 — TypedDict 는 키 집합이
닫혀 있어서, 두 쪽에 없는 키를 섞으면 유니온의 어느 멤버에도 맞지 않는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clawops.types.message_params import (
        KakaoSendParam,
        MessageCreateParams,
        MessageListParams,
    )

    _KAKAO: KakaoSendParam = {"channel_id": "clx9kak0001", "template_id": "clx9tpl0001"}

    # ── 통과해야 하는 것 ──────────────────────────────────────
    _text: MessageCreateParams = {"to": "010", "from_": "070", "body": "안녕하세요"}
    _text_lms: MessageCreateParams = {
        "to": "010", "from_": "070", "body": "긴 본문", "type": "lms", "subject": "제목",
    }
    _kakao: MessageCreateParams = {"to": "010", "from_": "070", "kakao": _KAKAO}
    _kakao_full: MessageCreateParams = {
        "to": "010", "from_": "070", "kakao": _KAKAO, "type": "ata",
        "fallback": {"body": "주문이 접수되었습니다.", "type": "lms"},
    }

    # ── 막아야 하는 것 ────────────────────────────────────────
    # 키를 섞으면 유니온의 **어느 멤버에도** 맞지 않아 dict 로 떨어진다
    # (그래서 typeddict-item 이 아니라 assignment 다).

    # body 와 kakao 를 같이 실을 수 없다 (400 kakao_body_not_allowed).
    _both: MessageCreateParams = {  # type: ignore[assignment]
        "to": "010", "from_": "070", "body": "안녕하세요", "kakao": _KAKAO,
    }

    # 알림톡에 첨부는 없다 (400 kakao_media_not_allowed).
    _kakao_media: MessageCreateParams = {  # type: ignore[assignment]
        "to": "010", "from_": "070", "kakao": _KAKAO, "media_url": ["https://e.com/a.jpg"],
    }

    # 알림톡에 제목은 없다 (400 kakao_subject_not_allowed).
    _kakao_subject: MessageCreateParams = {  # type: ignore[assignment]
        "to": "010", "from_": "070", "kakao": _KAKAO, "subject": "제목",
    }

    # fallback 은 알림톡에만 있다.
    _fallback_on_text: MessageCreateParams = {  # type: ignore[assignment]
        "to": "010", "from_": "070", "body": "안녕하세요", "fallback": {"body": "x"},
    }

    # kakao 를 실으면서 다른 Type 을 명시할 수 없다 (400 kakao_type_conflict).
    _kakao_sms: MessageCreateParams = {
        "to": "010", "from_": "070", "kakao": _KAKAO,
        "type": "sms",  # type: ignore[typeddict-item]
    }

    # `"ata"` 인데 kakao 가 없으면 400 이다 (ata 와 Kakao 는 서로를 요구한다).
    _ata_without_kakao: MessageCreateParams = {
        "to": "010", "from_": "070", "body": "안녕하세요",
        "type": "ata",  # type: ignore[typeddict-item]
    }

    # 채널/템플릿 ID 는 필수다.
    _kakao_no_template: MessageCreateParams = {
        "to": "010", "from_": "070",
        "kakao": {"channel_id": "clx9kak0001"},  # type: ignore[typeddict-item]
    }

    # ── 목록 필터 ─────────────────────────────────────────────
    _list_ata: MessageListParams = {"type": "ata", "number": "07052358010"}

    # 서버 쿼리 검증이 sending 을 받지 않는다 — 보내면 400 이다.
    _list_sending: MessageListParams = {"status": "sending"}  # type: ignore[typeddict-item]
