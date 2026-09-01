"""카카오 알림톡 발송 — 채널 찾기 → 템플릿 고르기 → 발송.

실행:
    CLAWOPS_API_KEY=sk_... CLAWOPS_ACCOUNT_ID=AC... python examples/kakao_ata_send.py 01012345678

알림톡은 **검수된 템플릿**으로만 나갑니다. 본문을 요청으로 쓰는 게 아니라,
승인된 템플릿에 변수를 채워 보냅니다. 채널 연결과 템플릿 검수는 콘솔에서
먼저 마쳐 두세요.
"""

from __future__ import annotations

import sys

from clawops import BadRequestError, ClawOps, UnprocessableEntityError


def main(to: str) -> None:
    client = ClawOps()  # CLAWOPS_API_KEY / CLAWOPS_ACCOUNT_ID

    # ── 1. 연결된 채널 ────────────────────────────────────────
    # list() 는 저장된 연결 정보를 그대로 준다(빠르다). 카카오 쪽 상태까지
    # 확인하려면 retrieve() 를 쓴다.
    channels = client.kakao.channels.list(status="connected")
    if not channels.data:
        print("연결된 카카오 채널이 없습니다. 콘솔에서 채널을 먼저 연결하세요.")
        return
    channel = channels.data[0]
    print(f"채널: {channel.name} (@{channel.search_id}) id={channel.id}")

    # ── 2. 발송 가능한 템플릿 ─────────────────────────────────
    # ⚠️ status 가 "APPROVED" 여도 휴면이면 못 보낸다. 정본은 sendable 이다.
    templates = list(client.kakao.templates.list(channel_id=channel.id).auto_paging_iter())
    sendable = [t for t in templates if t.sendable]
    if not sendable:
        print(f"발송 가능한 템플릿이 없습니다 (전체 {len(templates)}개).")
        for t in templates:
            print(f"  - {t.name}: status={t.status} dormant={t.dormant}")
        return

    template = sendable[0]
    print(f"템플릿: {template.name} id={template.id}")
    print(f"  본문: {template.content}")
    print(f"  변수: {template.variables}")

    # 템플릿이 요구하는 변수를 **전부** 채운다. 빠지면 400 kakao_variable_missing,
    # 없는 걸 주면 400 kakao_variable_unknown 이다. 키는 '고객명' 과 '#{고객명}'
    # 둘 다 받는다.
    variables = {name: "홍길동" for name in template.variables}

    # ── 3. 발송 ──────────────────────────────────────────────
    try:
        msg = client.messages.create(
            to=to,
            from_=_pick_sender(client),
            kakao={
                "channel_id": channel.id,
                "template_id": template.id,
                "variables": variables,
            },
            # 알림톡이 실패하면 이 문구가 문자로 나간다. 생략하면 템플릿 본문을
            # 그대로 보낸다. 대체 발송은 **별도 1건**으로 문자 단가가 청구된다.
            fallback={"body": "주문이 접수되었습니다."},
        )
    except BadRequestError as e:
        # 사유는 한글 문구가 아니라 code 로 분기한다.
        print(f"발송 거절 ({e.code}): {e.message}")
        return
    except UnprocessableEntityError as e:
        print(f"발송 불가 ({e.code}): {e.message}")
        return

    print(f"발송됨: {msg.message_id} type={msg.type} status={msg.status}")
    print(f"  본문(변수 치환 결과): {msg.body}")

    # 조회도 확인 — type='ata' 가 그대로 파싱된다.
    fetched = client.messages.get(msg.message_id)
    print(f"조회: {fetched.message_id} {fetched.status}")


def _pick_sender(client: ClawOps) -> str:
    """계정에 등록된 번호 하나를 발신번호로 쓴다."""
    numbers = client.numbers.list()
    if not numbers:
        raise SystemExit("발신에 쓸 번호가 없습니다.")
    return numbers[0].number


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("사용법: python examples/kakao_ata_send.py <수신번호>")
    main(sys.argv[1])
