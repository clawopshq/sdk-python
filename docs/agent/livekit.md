# LiveKit Agents 실행 (실험적)

[LiveKit Agents](https://docs.livekit.io/agents/) 로 작성한 음성 에이전트를 **LiveKit
서버도 SIP 도 없이** 실제 ClawOps 전화번호로 실행합니다.

> 실험적 기능이라 API 가 바뀔 수 있고, 동시통화는 현재 1건입니다.

## 설치

```bash
pip install 'clawops[livekit]'
pip install 'livekit-plugins-openai' 'livekit-plugins-cartesia'   # 쓰는 플러그인만
```

Python 3.10+ 필요, Alpine(musl) 미지원.

## 예제

```python
import asyncio

from livekit.agents import Agent, AgentSession
from livekit.plugins import cartesia, openai

from clawops.agent import ClawOpsAgent
from clawops.agent.livekit import LiveKitSession


async def create(call):          # 통화당 1회 호출
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(modalities=["text"]),
        tts=cartesia.TTS(model="sonic-3.5", language="ko"),
    )
    return session, Agent(instructions="당신은 친절한 예약 상담원입니다.")


agent = ClawOpsAgent(from_="07012341234", session=LiveKitSession(create))
asyncio.run(agent.serve())       # 착신 대기. 발신은 await agent.call(to=...)
```

`create` 가 `(AgentSession, Agent)` 를 반환하는 것이 전부입니다. `Agent` 서브클래스,
[`@function_tool`](https://docs.livekit.io/agents/build/tools/), `on_enter`, handoff 등
LiveKit 코드는 그대로 씁니다. 전체 스크립트: [`examples/livekit_agent.py`](../../examples/livekit_agent.py).

## 무엇이 되고 안 되나

ClawOps 는 room 없는 transport 라, LiveKit 기능 중 room/서버에 묶인 것은 안 됩니다.

| | |
|---|---|
| ✅ **그대로** | `Agent` 서브클래스(`on_enter`/`@function_tool`/handoff/`llm_node`·`tts_node` 오버라이드) · `AgentSession(...)` · `RunContext`(`userdata`) · `generate_reply`/`say`/`@session.on(...)` · `silero.VAD` · `inference.TurnDetector()`(한국어 지원) · Cartesia·Deepgram 등 HTTP 플러그인 |
| 🔧 **한 줄 수정** | `session.start(room=...)` → `room=` 을 빼세요. ClawOps 가 `start()` 를 대신 부릅니다 |
| ⚠️ **LiveKit 키 필요** | `inference.STT/LLM/TTS` 는 LiveKit Cloud 를 호출합니다. 없이 쓰려면 `openai.LLM(...)`/`cartesia.TTS(...)` 로 provider 직접 지정 |
| ❌ **불가** | `noise_cancellation.BVC()` · `ctx.api.*` · `ctx.wait_for_participant()` · `RoomOptions`/`AudioInputOptions` · LiveKit SIP 기반 `WarmTransferTask`/`AMD` · 아바타 |

## 알아둘 점

- **통화 제어 도구** `hang_up`/`collect_dtmf`/`send_dtmf`/`transfer_call` 이 자동
  주입됩니다. `ClawOpsAgent(builtin_tools=...)` 로 켜고 끄며, 유저 도구와 이름이
  겹치면 내장 쪽을 뺍니다. `transfer_call` 은 ClawOps 전환(PSTN/SIP)을 씁니다.
- **`modalities=["text"]` 인데 `tts` 가 없으면** 소리가 안 납니다. ClawOps 는 이 경우
  시작 시점에 `ValueError` 를 던집니다.
- **녹음·telemetry** 는 LiveKit `record=` 가 아니라 ClawOps 자체 기능을 씁니다.
- **동시통화 1건** — 현재는 한 번에 한 통화만 처리합니다.
