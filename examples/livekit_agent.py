#!/usr/bin/env python3
"""LiveKit Agents 를 ClawOps 전화망에서 실행하는 로컬 테스트 스크립트.

LiveKit 서버도 SIP 도 없이, 관용적인 LiveKit 코드를 실제 ClawOps 번호에 얹는다.

── 설치 ──────────────────────────────────────────────────────────
    pip install 'clawops[livekit]'
    pip install 'livekit-plugins-openai' 'livekit-plugins-cartesia'

── 환경변수 ──────────────────────────────────────────────────────
    export CLAWOPS_API_KEY="sk_..."         # 필수
    export CLAWOPS_ACCOUNT_ID="AC..."        # 필수
    export CLAWOPS_FROM="07012341234"        # 필수 (에이전트가 받을/걸 번호)
    export OPENAI_API_KEY="sk-..."           # 필수 (realtime 모델)
    export CARTESIA_API_KEY="sk_car_..."     # 선택 (있으면 음색을 Cartesia 로)

    # 아웃바운드로 테스트하려면 (없으면 착신 대기)
    export CLAWOPS_TO="01012345678"

── 실행 ──────────────────────────────────────────────────────────
    python examples/livekit_agent.py

    - CLAWOPS_TO 가 없으면: 착신 대기(serve). CLAWOPS_FROM 번호로 전화를 걸면 응답한다.
    - CLAWOPS_TO 가 있으면: 그 번호로 발신한다.

CARTESIA_API_KEY 가 있으면 realtime 모델은 텍스트만 만들고 음성은 Cartesia(sonic-3.5)
가 낸다. 없으면 OpenAI realtime 이 음성을 직접 낸다.
"""

from __future__ import annotations

import asyncio
import logging
import os

from livekit.agents import Agent, AgentSession, RunContext, function_tool
from livekit.plugins import openai

from clawops.agent import ClawOpsAgent
from clawops.agent.livekit import LiveKitSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("livekit-example")


# ── 유저 도구: 관용적인 LiveKit @function_tool 이 그대로 동작한다 ──


@function_tool
async def get_business_hours(ctx: RunContext, day: str) -> str:
    """영업 시간을 알려준다.

    Args:
        day: 요일 (예: '월요일', '토요일')
    """
    if day in ("토요일", "일요일"):
        return f"{day}은 휴무입니다."
    return f"{day}은 오전 9시부터 오후 6시까지 영업합니다."


# ── 유저 Agent 서브클래스: on_enter 인사말 ──────────────────────


class ReceptionAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "당신은 '클로숍 카페'의 친절한 전화 상담원입니다. "
                "손님의 문의(영업시간, 예약 등)에 짧고 자연스럽게 응대하세요. "
                "대화가 끝나면 hang_up 도구로 통화를 종료하세요."
            ),
            tools=[get_business_hours],
        )

    async def on_enter(self) -> None:
        # session.start() 안에서 호출된다 — 첫 인사를 만든다.
        self.session.generate_reply(instructions="전화를 받았음을 알리고 무엇을 도와드릴지 물어보세요.")


# ── 통화당 1회: (AgentSession, Agent) 를 만든다 ──────────────────


async def create(call):  # call: CallSession | None (prewarm 중엔 None)
    if os.environ.get("CARTESIA_API_KEY"):
        from livekit.plugins import cartesia

        # realtime 은 텍스트만, 음성은 Cartesia 가 낸다.
        session = AgentSession(
            llm=openai.realtime.RealtimeModel(modalities=["text"]),
            tts=cartesia.TTS(model="sonic-3.5", language="ko", voice="4dd4630e-19e0-4243-bca0-676ff85119b7"),
        )
        log.info("세션: OpenAI realtime(text) + Cartesia TTS")
    else:
        # OpenAI realtime 이 음성을 직접 낸다.
        session = AgentSession(
            llm=openai.realtime.RealtimeModel(voice="marin"),
        )
        log.info("세션: OpenAI realtime(audio)")

    return session, ReceptionAgent()


async def main() -> None:
    from_number = os.environ.get("CLAWOPS_FROM")
    if not from_number:
        raise SystemExit("CLAWOPS_FROM 환경변수를 설정하세요 (에이전트 번호).")

    agent = ClawOpsAgent(
        from_=from_number,
        session=LiveKitSession(create),
        builtin_tools=["hang_up"],  # 이 예제는 hang_up 만 켠다
    )

    @agent.on("call_start")
    async def _on_start(call) -> None:
        log.info("통화 시작: %s -> %s", call.from_number, call.to_number)

    @agent.on("call_end")
    async def _on_end(call) -> None:
        log.info("통화 종료: %s (%.1fs)", call.call_id, call.duration)

    to_number = os.environ.get("CLAWOPS_TO")
    if to_number:
        log.info("아웃바운드: %s 로 발신합니다...", to_number)
        await agent.connect()
        try:
            call = await agent.call(to=to_number)
            await call.wait()  # 통화가 끝날 때까지 대기
        finally:
            await agent.disconnect()
    else:
        log.info("착신 대기: %s 로 전화를 거세요. (Ctrl+C 로 종료)", from_number)
        await agent.serve()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("종료합니다.")
