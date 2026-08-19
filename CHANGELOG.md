# Changelog

## 0.46.1 (2026-08-19)

### Fixed
- **AI 음성이 발화 도중 짧게 끊기던 것.** 세그먼트가 끝날 때 남는 자투리(160바이트 미만)를 SDK 가 **무음으로 채워** 프레임을 맞추고 있었습니다. 세그먼트가 문장 끝이면 무해하지만 **말 도중에 끊기면 그 자리에 0~19ms 짜리 구멍**이 박힙니다.
  - 실통화 녹음 실측(2026-08-19): 발화 중 갭의 끝 위치가 **11/11 · 27/27 모두 `mod 160 = 0`**, 길이는 전부 160 미만, 경계 진폭 3132 — 우연히 정렬될 수 없는 지문이었습니다.
  - 이제 자투리를 **그대로** 보냅니다. 「뒤에 오디오가 더 오는가」는 큐를 들고 있는 서버만 아는 정보라, 프레이밍 판단을 그쪽으로 넘깁니다. 서버는 뒤따르는 mark 를 세그먼트 끝 신호로 읽어 그 자리에서 채웁니다.
  - 파이프라인 경로(`_pipeline_session`)는 **delta 마다** 잘라 채우고 있어 빈도가 더 높았습니다. 이쪽도 원문 그대로 보냅니다.
  - ⚠️ **서버 배포가 선행되어야 합니다.** 자투리를 받아 처리하는 서버(2026-08-19 배포)가 아니면 짧은 RTP 패킷이 그대로 송출됩니다. 되돌려야 하면 `CLAWOPS_TAIL_PAD=1` 로 종전 동작을 켤 수 있습니다.

## 0.46.0 (2026-08-15)

### Added
- **`CallSession.ended_duration` — 서버가 확정한 통화 시간.** 종료 이벤트가 실어 보내던 값을 지금까지 SDK 가 읽지 않아, 통화 기록을 자체 시스템에 적재하려면 REST 를 다시 조회하거나 로컬 시계로 잰 값을 써야 했습니다. 이제 종료 이벤트 하나로 기록을 마칠 수 있습니다.
  ```python
  @agent.on("call_end")
  async def on_end(call):
      print(call.ended_status, call.ended_duration)   # completed 91
  ```
  - `duration` 은 그대로 둡니다 — 그쪽은 **SDK 가 로컬 시계로 재는 경과 시간**이라 통화 중에도 읽히고, 세션이 붙기 전후의 오차를 포함합니다. 기록·정산에 쓸 값은 `ended_duration` 입니다.
  - `call_end` 핸들러 안에서 읽을 수 있습니다. 서버는 미디어 스트림을 먼저 닫고 정리를 마친 뒤에 종료 정보를 보내므로, SDK 가 그 프레임을 **짧게 기다렸다가** `call_end` 를 발화합니다(최대 2초). 정상적인 통화에서는 밀리초 안에 끝나고, 제어 연결이 끊긴 경우에만 상한을 씁니다.
  - 서버가 값을 보내지 않으면 `None` 을 유지합니다. **서버 배포가 선행되어야** 실제 값이 들어옵니다 — 그 전까지는 `None` 입니다.

## 0.45.1 (2026-08-14)

### Fixed
- **LiveKit 을 쓰면 `caller_id_mode` 가 없던 것.** 0.45.0 이 `transfer()` 와 내장 도구 스키마에는 넣었는데, LiveKit 경로는 `transfer_call` 을 따로 정의하고 있어 그쪽만 빠졌습니다. LiveKit 사용자는 AI 가 전환 발신번호를 고를 수 없었습니다.
- 잘못된 `caller_id_mode` 값을 LiveKit 도구에서도 전환을 걸기 **전에** 거절합니다(다른 인자와 같은 규칙).

### Changed
- 개발용 의존성(`clawops[dev]`)에 `livekit-agents` 를 추가했습니다. 없으면 `tests/agent/test_livekit_*.py` 43개가 **항상 건너뛰어져** LiveKit 표면이 검증되지 않은 채 초록불로 보입니다 — 위 결함이 그렇게 새어 나갔습니다.

## 0.45.0 (2026-08-14)

### Added
- **`transfer(caller_id_mode=...)` — 전환받는 쪽에 표시될 번호를 고릅니다.** 지금까지 전환은 **계정 보유번호**(인바운드면 착신 070)로 고정이었고, 원 발신자 번호를 보이게 하려면 `caller_id` 에 번호를 직접 넘기는 수밖에 없었습니다.
  ```python
  await call.transfer("021234567", caller_id_mode="original")   # 환자 번호가 데스크에 표시
  ```
  - `"original"` 은 **선호**입니다. 승계할 수 없는 통화(통신사 직결 인바운드가 아니거나 국내 번호로 정규화되지 않는 발신번호)면 조용히 계정 번호로 내려앉고 **전환은 그대로 성사됩니다**.
  - `caller_id` 로 번호를 직접 주는 것은 **지시**라 성격이 다릅니다. 허용 범위(계정 보유번호 또는 그 통화의 원 발신자)를 벗어나면 전환 자체가 실패합니다. 원 발신자 번호를 직접 넘기던 코드는 승계 불가 통화에서 전환이 실패했는데, `caller_id_mode` 로 바꾸면 그 경우에도 연결됩니다.
  - 둘 다 주면 `caller_id` 가 이기고 `caller_id_mode` 는 무시됩니다.
  - 내장 `transfer_call` 도구에도 `caller_id_mode` 가 추가되어 AI 가 번호 대신 의도를 고를 수 있습니다.
  - 기본 동작은 바뀌지 않습니다. 지정하지 않으면 지금까지와 똑같이 계정 번호가 표시됩니다.

### Fixed
- 내장 `transfer_call` 도구가 건 전환이 실패했을 때 아무 흔적도 남지 않던 것. 결과를 기다리지 않는 구조라 예외가 태스크 안에 갇혔습니다. 이제 로그에 남습니다.

## 0.44.0 (2026-08-14)

### Added
- **`session_factory=` — 한 프로세스로 동시 통화를 받습니다.** 통화마다 세션을 새로 만들어 대화 이력·오디오 경로·진행 중인 작업이 그 통화 안에 갇힙니다. 번호를 여러 개 운영하더라도 번호별로 프로세스를 나눌 필요가 없습니다.
  ```python
  agent = ClawOpsAgent(
      from_="07012341234",
      session_factory=lambda: OpenAIRealtime(system_prompt="..."),
  )
  ```
  LiveKit 은 `session_factory=lambda: LiveKitSession(create)` 로 넘깁니다. HTTP 플러그인용 `http_context` 도 통화별로 열립니다.

### Fixed
- **동시 통화가 서로를 덮어쓰던 것.** `session=` 으로 넘긴 객체를 모든 통화가 공유했습니다. 두 번째 통화가 시작되면 첫 통화의 **대화 이력이 초기화되고 음성이 두 번째 통화 쪽으로 나갔으며**, 첫 통화가 끝날 때의 정리가 두 번째 통화를 내렸습니다. `session_factory=` 로 바꾸면 해결됩니다. `session=` 은 그대로 동작하지만 동시 통화 1건까지만 안전하며, 두 번째 통화가 시작되면 원인을 지목하는 에러 로그를 남깁니다.
- **실패한 발신의 정리가 다른 통화를 끊던 것.** 응답 전에 실패한 통화를 정리하면서 공유 세션을 종료해, 그때 진행 중이던 다른 통화가 무음이 됐습니다.

### Changed
- `session` 과 `session_factory` 중 **정확히 하나**를 지정해야 합니다. 둘 다 없거나 둘 다 있으면 `AgentError` 입니다(예전에는 `session` 이 필수 인자라 `TypeError` 였습니다).

## 0.43.1 (2026-08-14)

### Fixed
- **키패드 입력이 통화 사이에서 섞이던 것.** `collect_dtmf()` 로 받지 않는 입력(=AI 가 알아서 듣는 키패드)의 버퍼가 통화가 아니라 `ClawOpsAgent` 인스턴스에 붙어 있었습니다. 한 프로세스가 통화 두 건을 처리할 때 두 발신자가 0.5초 안에 키를 누르면 **입력이 한 덩어리로 합쳐지고, 그 덩어리가 나중에 누른 통화에만** 전달됐습니다 — 다른 통화는 입력을 통째로 잃습니다. 이제 통화마다 자기 버퍼를 씁니다.
- **주입 중이던 입력이 다음 키에 잘리던 것.** 앞서 누른 숫자를 AI 에게 전달하는 중에 다음 키를 누르면 그 전달이 취소돼 앞 숫자가 사라졌습니다. 통화가 한 건일 때도 나던 문제입니다.
- 통화가 끝난 뒤 뒤늦게 깨어난 입력 처리가 아무 일도 하지 않습니다.

## 0.43.0 (2026-08-14)

### Fixed
- **긴 전환 통화가 제어 연결을 죽이던 것 — 업그레이드를 권합니다.** `transfer_call()` 로 넘긴 통화가 40초를 넘기면 대기가 취소되고, 뒤늦게 도착한 완료 이벤트가 그 대기를 건드려 예외를 던졌습니다. 그 예외가 수신 루프 밖으로 새어 **제어 연결 태스크가 죽고 재접속이 일어나지 않았습니다** — 이후 걸려오는 전화가 전부 폴백으로 넘어갑니다(실제 사고: 16시간). 병원 데스크 상담처럼 전환 통화가 1분을 넘는 것은 흔한 일이라, 전환을 쓰신다면 사실상 상시 노출돼 있었습니다.
  - `timeout` 은 이제 **대상이 전화를 받기까지**의 대기에만 적용됩니다(문서가 원래 설명하던 동작). 받은 뒤의 통화가 얼마나 길어지든 `transfer_call()` 은 전환이 끝날 때까지 기다렸다가 결과를 돌려줍니다.
  - 이벤트 처리 중 예외가 나도 제어 연결이 유지되고, 예외 종류와 무관하게 재접속합니다. 예전에는 일부 예외만 재접속 대상이었습니다.
  - 결과가 중복·지연 도착해도 예외가 나지 않습니다.
- **같은 통화에서 전환을 다시 걸면 이전 대기를 잃던 것.** `after_transfer="return"` 은 전환이 끝나면 AI 로 돌아오므로 한 통화에서 여러 번 전환할 수 있는데, 2차 요청이 1차 대기를 덮어써 1차 `transfer_call()` 이 영영 반환되지 않았습니다. 이제 요청마다 상관 ID 를 붙여 각자 자기 결과를 받습니다.
  - 서버가 이 ID 를 되돌려야 하므로 **서버 배포가 선행되어야** 합니다. 아직이면 통화 단위 폴백으로 동작합니다(그 동안에는 한 통화에 전환 하나만 구분 가능).
- 통화가 먼저 끝나면 남은 전환 대기를 정리합니다. 종료 통지와 전환 결과는 서로 다른 요청으로 전달되어 도착 순서가 뒤바뀔 수 있으므로, 짧은 유예를 두고 정리해 정상 결과를 버리지 않습니다.

## 0.42.0 (2026-08-14)

### Added
- **`calls.create(agent_id=)` — 매니지드 에이전트로 발신.** 콘솔에서 만든 AI 에이전트에게 아웃바운드 통화를 맡긴다. REST 는 `AgentId` 를 계속 지원해 왔는데 SDK 에만 파라미터가 없어, 0.38.0 에서 AI Completion 모드를 걷어낸 뒤로 **SDK 로 AI 통화를 거는 방법이 `url=`(VoiceML 서버 직접 구현)뿐**이었다. 그 공백을 메운다.
  - `call_context={"instruction": ..., "variables": {...}}` — **이번 통화에만** 적용되는 지시. 에이전트 자체 설정은 그대로 두고 이 통화만 다르게 행동시킨다. 같은 에이전트로 동시에 거는 다른 통화에는 영향이 없다. 파라미터는 snake_case 로 받고 본문은 PascalCase 로 보낸다(스펙이 `additionalProperties: false` 라 snake_case 를 그대로 흘리면 400).
- **`calls.create(call_flow_id=, variables=)` — 콜 플로우로 발신.** 콘솔 빌더로 만든 결정적 ARS 플로우가 통화를 진행한다. `variables` 는 멘트·URL·본문의 `{{이름}}` 을 치환하며 `call_flow_id` 와 함께일 때만 쓸 수 있다(단독 지정 시 400). `caller`·`callee`·`recording_url`·`recording_duration`·`http_status` 는 통화 중 자동으로 채워지는 예약 변수라 지정할 수 없다.
- `url`·`agent_id`·`call_flow_id` 는 서로 배타적이고, **셋 다 생략하면 Agent SDK 모드**로 From 번호에 연결된 세션이 받는다.

### Fixed
- `CallCreateParams` 에 `machine_detection` 이 빠져 있던 것. 메서드 시그니처에는 있었으나 TypedDict 에 없어 타입 힌트만 어긋나 있었다.

## 0.40.0 (2026-07-31)

### Added
- **수신거부(DNC) 명단 리소스 — `client.blocked_recipients`.** 광고 문자 하단의 080 무료수신거부, ARS 의 "수신거부 9번", 상담 중 구두 요청 등으로 접수된 번호를 계정 단위로 관리한다. 등록된 번호는 그 계정의 **발신**(전화·문자)에서 제외되며 **착신은 막지 않는다** — 수신거부 접수 자체가 우리 080/ARS 로 오는 착신이기 때문이다.
  - `create(number=, channel=)` — 하이픈·`+82` 표기 모두 허용되며 국내 표기로 정규화되어 저장된다. **멱등**이라 이미 차단 중인 (번호, 채널)을 다시 등록해도 에러가 아니라 기존 항목을 돌려준다(같은 사람이 수신거부를 두 번 요청하는 것은 정상 상황이다).
  - `list(channel=, number=, status=)` — 기본은 차단 중인 것만. `status="released"|"all"` 로 해제 이력까지 조회. `number` 는 하이픈 표기로 넣어도 정규화 후 대조한다.
  - `retrieve(block_id)` / `update(block_id, note=)` — 메모만 수정한다. 번호·채널은 바꿀 수 없다(증빙이 뒤틀린다).
  - `release(block_id)` — 해제. **항목을 삭제하지 않고** `active=False` + `unblocked_at` 을 기록해 이력으로 남긴다. 언제 거부했고 언제 풀렸는지가 곧 증빙이라서다. 재호출해도 최초 해제 시각은 덮지 않는다.
  - 전화와 문자는 각각 따로 차단한다. 같은 번호라도 채널마다 별개 항목이라 둘 다 막으려면 `channel` 을 바꿔 두 번 등록한다.
- 내부: `_base_client` 에 `_patch` / `_delete_with_response` 추가(sync·async). 후자는 soft delete 처럼 삭제 결과 리소스를 그대로 반환하는 endpoint 용으로, 응답을 버리는 기존 `_delete` 는 그대로 둔다.

## 0.38.0 (2026-07-23)

### Removed
- **`calls.create(ai=...)` — AI Completion 모드 제거.** 서버에서 해당 모드가 종료되어 `AI` 필드를 포함한 요청은 이제 `410 ai_mode_removed` 로 거절된다. `ai` 파라미터와 `AIConfigParam`/`OpenAIAIConfigParam`/`GeminiAIConfigParam` 타입을 삭제했다. 통화에 AI 를 태우려면 **Agent SDK**(`clawops.agent`) 를 쓰거나, 콘솔에서 만든 매니지드 에이전트 또는 VoiceML(`url=`) 을 사용한다.

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
