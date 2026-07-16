"""LiveKit Agents 를 ClawOps 전화망 위에서 실행하기 위한 어댑터.

`pip install 'clawops[livekit]'` 로 설치한다.

유저는 관용적인 LiveKit 코드를 그대로 쓰고, ClawOps 는 전화 transport 만 공급한다 —
LiveKit 서버도 SIP 도 필요 없다.

    from livekit.agents import Agent, AgentSession
    from livekit.plugins import cartesia, openai

    from clawops.agent import ClawOpsAgent
    from clawops.agent.livekit import LiveKitSession

    async def create(call):
        session = AgentSession(
            llm=openai.realtime.RealtimeModel(modalities=["text"]),
            tts=cartesia.TTS(model="sonic-3.5", language="ko"),
        )
        return session, Agent(instructions="친절한 상담원입니다.")

    agent = ClawOpsAgent(from_="07012341234", session=LiveKitSession(create))

무엇이 그대로 되고 무엇이 안 되는지는 docs/agent/livekit.md 를 볼 것.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._io import ClawOpsAudioInput, ClawOpsAudioOutput
    from ._session import LiveKitSession
    from ._toolset import ClawOpsPhoneTools

__all__ = [
    "LiveKitSession",
    "ClawOpsPhoneTools",
    "ClawOpsAudioInput",
    "ClawOpsAudioOutput",
]

_LAZY = {
    "LiveKitSession": "._session",
    "ClawOpsPhoneTools": "._toolset",
    "ClawOpsAudioInput": "._io",
    "ClawOpsAudioOutput": "._io",
}


def __getattr__(name: str) -> Any:
    """livekit-agents 미설치 시 ImportError 를 명확한 메시지로 바꾼다."""
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    import importlib

    try:
        mod = importlib.import_module(module, __name__)
    except ImportError as e:  # pragma: no cover - 설치 안내 경로
        raise ImportError(
            "clawops.agent.livekit 는 livekit-agents 가 필요합니다. "
            "설치: pip install 'clawops[livekit]'"
        ) from e
    return getattr(mod, name)
