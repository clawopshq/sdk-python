# 이벤트 & CallSession

## 이벤트 핸들러

`@agent.on()` 데코레이터로 통화 이벤트를 수신합니다.

```python
@agent.on("call_start")
async def on_call_start(call):
    print(f"통화 시작: {call.from_number} -> {call.to_number}")
    print(f"통화 ID: {call.call_id}")

@agent.on("call_end")
async def on_call_end(call):
    print(f"통화 종료: {call.call_id} (총 {call.duration:.1f}초)")

@agent.on("transcript")
async def on_transcript(call, role, text):
    print(f"[{role}] {text}")
    # role: "user" (고객 음성 인식) 또는 "assistant" (AI 응답)
```

### 이벤트 목록

| 이벤트 | 파라미터 | 설명 |
|--------|----------|------|
| `call_start` | `(call)` | 통화 시작 — **상대가 받은 뒤** 미디어 세션이 열릴 때 |
| `call_end` | `(call)` | 통화 종료 — `call_start` 가 발화된 통화만 |
| `transcript` | `(call, role, text)` | 음성 텍스트 생성 |
| `dtmf` | `(call, digit)` | DTMF 키 입력 수신 |

> **주의 — 연결되지 않은 통화에서는 아무 이벤트도 발화되지 않습니다.**
> `call_start`/`call_end` 는 통화가 **응답된 뒤** 열리는 미디어 세션에 묶여 있습니다. 상대가 받지 않았거나
> (무응답) 통화중·거절이면 두 이벤트 모두 발화되지 않고, `await call.wait()` 만 조용히 리턴합니다.
>
> SDK 에 `call_failed` 이벤트 타입이 정의되어 있지만 **현재 서버는 이 이벤트를 보내지 않습니다.**
> 핸들러를 등록해도 호출되지 않으니 발신 실패 감지에 사용하지 마세요.
> 발신 결과는 [발신 결과 확인하기](quickstart.md#발신-결과-확인하기) 를 참고하세요.

## CallSession

개별 통화의 상태를 관리합니다. 이벤트 핸들러의 `call` 파라미터로 전달됩니다.

### 속성

| 속성 | 타입 | 설명 |
|------|------|------|
| `call_id` | `str` | 통화 ID |
| `from_number` | `str` | 발신 번호 |
| `to_number` | `str` | 수신 번호 |
| `account_id` | `str` | 계정 ID |
| `direction` | `str` | `"inbound"` 또는 `"outbound"` |
| `status` | `str` | 아래 표 참고 |
| `start_time` | `datetime` | 통화 시작 시간 |
| `duration` | `float` | 통화 경과 시간 (초) |
| `metadata` | `dict` | 사용자 정의 메타데이터 |

#### `status` 값

| 값 | 시점 |
|----|------|
| `queued` | 발신(outbound) 세션 생성 직후 |
| `ringing` | 수신(inbound) 세션 생성 직후 / 발신은 통신망이 벨 신호를 올렸을 때 |
| `in-progress` | 발신 통화가 응답되어 미디어 세션이 열릴 때 |
| `completed` | **통화가 끝났을 때 — 응답 여부와 무관** |

> **주의:** `status` 는 SDK 내부 진행 상태이며 최종 결과가 아닙니다. 상대가 받지 않아 무응답으로 끝난 통화도
> `completed` 가 됩니다. `no-answer` / `busy` / `rejected` 같은 실제 종료 사유는
> [발신 결과 확인하기](quickstart.md#발신-결과-확인하기) 를 참고하세요.

### 메서드

```python
@agent.on("call_start")
async def on_start(call):
    call.metadata["customer_id"] = "CUST_123"

    await call.send_audio(pcm16_bytes)   # 오디오 전송
    await call.clear_audio()             # 오디오 큐 초기화 (인터럽트 시)
    await call.hangup()                  # 통화 종료
    await call.transfer("01012345678")   # 다른 번호로 통화 전환
    await call.wait()                    # 통화 종료까지 대기 (아웃바운드 시 유용)
```

> `await call.wait()`는 통화가 종료될 때까지 대기합니다. 주로 아웃바운드 단건 발신 시 통화가 끝나기를 기다리는 데 사용합니다.
> **상대가 받지 않아도(무응답) 발신 취소 시점에 리턴하며, 리턴했다는 사실만으로는 통화 성사 여부를 알 수 없습니다.**
