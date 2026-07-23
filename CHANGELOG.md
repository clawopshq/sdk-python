# Changelog

## 0.37.1 (2026-07-23)

### Fixed
- **발신 통화에서 `@agent.tool` 로 등록한 도구가 AI 에게 전달되지 않던 문제.** 발신은 originate 직후 prewarm 이 돌면서 LLM 에 tool 스키마를 확정 전송하는데(OpenAI `session.update` / Gemini Live connect config), 도구 주입은 상대가 받은 뒤인 `_start_call_session` 에서야 실행됐다. 즉 **유저 도구가 통째로 빠진 채 세션이 시작**되어, 아무리 유도해도 도구가 호출되지 않았다. 착신·`PipelineSession`·`LiveKitSession` 은 영향 없음. 이제 prewarm 전에 도구를 주입한다.
- MCP 도구는 통화 시작 시점에야 registry 에 붙으므로 prewarm 스키마에 없었다. OpenAI Realtime 은 `attach()` 에서 도구가 바뀐 경우에만 `session.update` 로 재전송한다. Gemini Live 는 연결 후 도구 변경이 불가능하므로, MCP 서버가 설정돼 있으면 prewarm 을 건너뛰고 기존 `start()` 경로로 간다.
- **prewarm 창(상대가 받기 전)에 내장 통화 제어 도구가 호출되면 크래시**했다. 이 구간의 통화 객체는 버퍼링 stub 이라 `hangup()` 이 없어 `AttributeError` 가 나고, tool 결과가 모델에 영영 돌아가지 않아 응답이 멈춘 채로 통화가 시작될 수 있었다. 이제 "통화가 아직 연결되지 않았습니다" 결과를 돌려줘 모델이 응답 후 다시 호출할 수 있다.

## 0.37.0 (2026-07-22)

### Fixed
- **발신 결과가 통보되지 않던 문제.** 서버는 `call.ended` 에 종료 사유를 `status` 로 싣지만 `_handle_ended` 가 이 값을 버리고 `_mark_ended()` 가 항상 `"completed"` 를 하드코딩해, **상대가 받지 않은 통화(무응답)가 성사된 통화와 구분되지 않았다.** `await session.wait()` 가 조용히 리턴하고 `status` 도 `completed` 라서 발신 실패를 코드로 감지할 방법이 아예 없었다. 이제 서버가 통보한 종료 사유(`completed` / `no-answer` / `busy` / `rejected` / `canceled` / `failed`)를 그대로 반영한다.
- `Call.status` 의 `Literal` 이 `queued`/`ringing`/`in-progress`/`completed`/`failed` 5종만 허용해, 정작 진단이 필요한 **무응답·통화중·거절 통화를 `client.calls.get()` 으로 조회하면 `ValidationError` 로 실패**했다. 서버가 실제로 반환하는 9종 전부를 허용하도록 넓혔다.

### Added
- `CallSession.ended_status` — 서버가 통보한 최종 종료 사유. 통화가 끝나기 전에는 `None`. `completed` 만이 실제로 연결된 통화를 의미한다.
- `call_failed` 이벤트가 실제로 발화된다. 통화가 **연결되지 못하고** 끝났을 때 `(call, reason)` 으로 호출되며 `reason` 은 `ended_status` 와 같다. 이전에는 서버가 보내지 않는 `call.failed` 에만 묶여 있어 영원히 호출되지 않는 죽은 API 였다. 이제 발신 한 건은 반드시 `call_start`+`call_end`(연결됨) 또는 `call_failed`(미연결) 중 한쪽으로 끝난다.

## 0.36.0 (2026-07-18)

### Added
- `MediaWebSocket` 와 `CallSession` 을 `clawops.agent` 의 public export 로 노출한다. mediaUrl 을 직접 받아 통화 하나를 굴리는 **멀티테넌트 서버 러너**(통화당 설정을 디스패치하는 워커)가 Control WS 없이 이 부품들을 조립할 수 있다. Node SDK(`@teamlearners/clawops/agent`)가 동일 부품을 열어둔 것과 대칭.
- `CallSession.bind_transport(...)` — 오디오/DTMF/hangup transport 배선을 캡슐화하는 public 메서드. 서버가 이미 열린 미디어 transport 로부터 통화를 구동할 때 `_send_audio_fn`/`_media_ws` 같은 내부 필드를 직접 대입하지 않아도 된다. 기존 `ClawOpsAgent` 경로도 이 메서드를 사용하도록 정리(동작 불변).

## 0.35.0 (2026-07-16)

### Added
- LiveKit transport 에서 `@agent.on("transcript")` 훅 지원. LiveKit `AgentSession` 의 최종 대화 항목(`conversation_item_added`)을 네이티브 세션과 동일하게 `transcript` 이벤트(`role`, `text`)로 흘려보낸다. 세션만 `LiveKitSession` 으로 바꿔도 트랜스크립트를 모아 후처리(요약·escalation 등)하던 기존 앱이 그대로 동작한다.
- 예제(`examples/livekit_agent.py`)에 xAI TTS(`livekit-plugins-xai`) 분기 추가 — `XAI_API_KEY` 가 있으면 OpenAI Realtime(text) + xAI TTS 조합으로 음색을 낸다. `voice`/`language` 는 문자열로 지정한다(예: `voice="iris"`, `language="ko"`).

## 0.34.0 (2026-07-16)

### Added
- `clawops[livekit]` extra 신설 — LiveKit Agents 로 작성한 에이전트를 LiveKit 서버·SIP 없이 ClawOps 전화망에서 실행한다(`LiveKitSession`). 관용적인 LiveKit 코드(`Agent` 서브클래스·`AgentSession(llm=,tts=,stt=,...)`·`@function_tool`)를 그대로 쓰고, `session.start(room=...)` 의 `room` 만 우리가 대신 처리한다. OpenAI Realtime(`modalities=["text"]`) + Cartesia/xAI 등 TTS 조합으로 음색을 교체할 수 있다. 착신(`serve()`)·발신(`call(to=)`)·prewarm·내장 전화 도구(hang_up/transfer/collect_dtmf/send_dtmf)·`@agent.tool` 브리지 지원. 기존 `OpenAIRealtime`/`GeminiRealtime`/`PipelineSession` 경로는 변경 없음. Python 전용 extra (`agent-all` 에는 미포함).

## 0.33.0 (2026-07-08)

### Added
- `ClawOpsAgent(machine_detection=...)` — 인스턴스 레벨 AMD default. 생성자에서 지정하면 해당 에이전트의 모든 발신에 적용된다(`"Enable"` / `"Hangup"` / `None`). `agent.call(to, machine_detection=...)` 의 호출별 override 는 그대로 유지되며, 우선순위는 **호출 인자 > 인스턴스 default > 비활성**. per-call `machine_detection`(0.29.0)의 편의 확장이며 서버 동작 변화는 없다.

## 0.32.0 (2026-07-07)

### Added
- `call.transfer(destination_type=...)` + `transfer_call` 도구에 `destination_type`(`pstn`/`sip`) 파라미터 추가. `'sip'` 이면 `to` 를 SIP URI(`sip:user@host`)로 해석해 통화를 PSTN carrier 없이 SIP 엔드포인트로 직접 전환한다(INVITE 브릿지 — 녹음·관측 유지). 기본값 `'pstn'` (기존 전화번호 전환과 하위호환). `'sip'` 전환은 `sip_trunk` 부가서비스가 필요하며, 미보유 시 전환은 실패하고 통화는 AI 로 유지된다.

## 0.31.0 (2026-06-22)

### Added
- `numbers.update` 에 인바운드 라우팅 파라미터 추가 — `routing_type`(`webhook`/`sip`/`softphone`), `sip_endpoint_id`, `sip_credential_id`. `softphone` 으로 등록된 SIP 단말 착신, `sip` 으로 외부 PBX 라우팅을 API 로 설정할 수 있다 (둘 다 `sip_trunk` 부가서비스 필요).
- `sip_credentials` / `sip_endpoints` 조회 전용 리소스 신설 (`list` / `get`, sync·async) — softphone/sip 라우팅 설정에 필요한 id 를 조회한다. 평문 password·ha1 은 응답에 포함되지 않는다.
- `PhoneNumber` 응답 모델에 `routing_type` / `sip_endpoint_id` / `sip_credential_id` 필드 추가.

## 0.30.0 (2026-06-10)

### Added
- `Call.answered_by` — AMD(`machine_detection`) 결과 필드 추가. `machine_detection` 을 켠 발신 통화에서 `human`(사람) / `machine`(자동응답기·음성사서함) / `unknown`(판정 불가) 값으로 채워진다 (`calls.get` / `calls.list` 응답). 미사용 통화는 `None`.
- README·agent quickstart 에 `machine_detection` 사용법과 `answered_by` / status callback `AnsweredBy` 확인 방법 문서화.

## 0.27.1 (2026-05-26)

### Fixed
- `clawops[openai]` extra 가 `openai>=2.0.0` 만 설치하여 OpenAI Realtime 사용 시 `You need to install openai[realtime]` 오류가 발생하던 문제 수정 — extra 를 `openai[realtime]>=2.0.0` 로 변경하여 websocket 전송 의존성을 함께 설치한다.

## 0.27.0 (2026-05-26)

### Added
- **Outbound realtime prewarm** — Realtime 세션(OpenAI / Gemini)을 발신(originate) 직후 ring 구간에 미리 연결하고 greeting 오디오를 prebuffer 하여, 상대가 받는 즉시 첫 음성을 송출한다. `answer → first-audio` 지연이 약 2.6s → ~0ms(prebuffer 즉시 flush) 수준으로 단축된다.
  - `ClawOpsAgent(prewarm_enabled=True)` (기본값) 로 통화 단위 on/off.
  - prewarm 트리거 우선순위: `agent.call()` originate 직후(주 경로) → `call.ringing`(fallback) → `call.outbound_ready`(최종 fallback). `call.ringing` 은 트렁크가 SIP 18x 를 올리지 않으면 도착하지 않을 수 있어 신뢰하지 않는다.
  - `[PREWARM-T]` 로그 마커(start / done / attach / first-audio)로 latency 측정. `scripts/measure_prewarm_cost.py` 로 A/B 측정.

### Fixed
- prewarm 후 attach 전에 통화가 실패/종료될 때 LLM WebSocket 연결을 `session.stop()` 으로 정리하여 leak 을 방지한다 (`_prewarm_attached` 가드로 정상 통화의 이중 stop 방지). originate-time prewarm 으로 미응답/거절 통화에서도 prewarm 연결이 열리므로 필수.
- OpenAI / Gemini Realtime `stop()` 이 receive loop task 를 cancel 후 `asyncio.gather(..., return_exceptions=True)` 로 수거하여 "Task exception was never retrieved" 경고를 제거한다 (현재 실행 중인 task 는 self-await 회피를 위해 제외).

### Known limitations
- `ClawOpsAgent` 1 인스턴스 = 동시 outbound 통화 1건 가정. 단일 공유 세션이므로 동시 다발 발신(같은 인스턴스)은 미지원.
