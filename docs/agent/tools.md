# Tool (함수 호출)

`@agent.tool` 데코레이터로 AI가 호출할 수 있는 함수를 등록합니다.

## 기본 사용법

```python
@agent.tool
async def check_order(order_id: str) -> str:
    """주문 상태를 확인합니다.

    고객이 주문 번호를 말하면 이 함수를 호출하세요.
    """
    order = await db.get_order(order_id)
    return f"주문 {order_id}는 {order.status} 상태입니다."
```

## 작성 규칙

- 함수는 반드시 `async`여야 합니다
- 반환 타입은 `str`이어야 합니다
- **docstring이 AI에게 함수 설명으로 전달됩니다** — 상세하게 작성하세요
- 파라미터 타입 힌트(`str`, `int`, `float`, `bool`)가 자동으로 JSON Schema로 변환됩니다
- 기본값이 있는 파라미터는 optional로 처리됩니다

```python
@agent.tool
async def search_products(
    query: str,            # required
    category: str = "",    # optional
    limit: int = 10,       # optional
) -> str:
    """상품을 검색합니다. 고객이 상품을 찾을 때 사용하세요."""
    results = await product_api.search(query, category, limit)
    return "\n".join(f"- {r.name}: {r.price}원" for r in results)
```

---

## Tool Config (도구 실행 설정)

`tool_config`로 도구 실행 시 동작을 설정합니다.

```python
from clawops.agent import ClawOpsAgent, ToolConfig

agent = ClawOpsAgent(
    from_="07012341234",
    session=session,
    tool_config=ToolConfig(
        hold_audio=True,
    ),
)
```

### Hold Audio (대기 음악)

AI가 도구를 실행하는 동안 고객에게 대기 음악을 재생합니다. 외부 API 호출 등 시간이 걸리는 도구 실행 중 무음을 방지합니다.

| 값 | 설명 |
| :--- | :--- |
| `True` | 기본 차임 멜로디 재생 (~10초 루프) |
| `"./hold_music.wav"` | WAV 파일 경로 (16-bit PCM, 자동 리샘플링/모노 변환) |
| `b"..."` | raw µ-law 오디오 데이터 |
| `False` | 비활성화 **(기본값)** |

```python
# 기본 차임 사용
agent = ClawOpsAgent(
    from_="07012341234",
    session=session,
    tool_config=ToolConfig(hold_audio=True),
)

# 커스텀 WAV 파일
agent = ClawOpsAgent(
    from_="07012341234",
    session=session,
    tool_config=ToolConfig(hold_audio="./hold_music.wav"),
)
```

---

## 내장 Tool (Built-in Tools)

Agent는 통화 제어를 위한 내장 도구를 기본 제공합니다. `BuiltinTool`을 사용해 어떤 내장 도구를 활성화할지 제어할 수 있습니다.

### 내장 도구 목록

| 도구            | 상수                        | 설명                                                                                                                    |
| :-------------- | :-------------------------- | :---------------------------------------------------------------------------------------------------------------------- |
| `hang_up`       | `BuiltinTool.HANG_UP`       | 전화를 종료합니다. AI가 대화 완료를 판단하면 자동으로 호출합니다.                                                       |
| `collect_dtmf`  | `BuiltinTool.COLLECT_DTMF`  | 사용자의 키패드(DTMF) 입력을 수집합니다. 본인 인증, 메뉴 선택 등에 사용합니다.                                          |
| `send_dtmf`     | `BuiltinTool.SEND_DTMF`     | DTMF 신호를 전송합니다. ARS 메뉴 탐색, 내선번호 입력 등에 사용합니다.                                                   |
| `transfer_call` | `BuiltinTool.TRANSFER_CALL` | 통화를 다른 번호로 전환합니다. Blind(즉시 전환)와 Warm(안내 후 전환) 모드를 지원합니다. 고객센터 연결, 상담원 전환에 사용합니다. |

### 선택 상수

| 상수               | 설명                               |
| :----------------- | :--------------------------------- |
| `BuiltinTool.ALL`  | 모든 내장 도구 활성화 **(기본값)** |
| `BuiltinTool.NONE` | 모든 내장 도구 비활성화            |

### 사용법

```python
from clawops.agent import ClawOpsAgent, BuiltinTool

# 기본: 모든 내장 도구 활성화
agent = ClawOpsAgent(
    from_="07012341234",
    session=session,
)

# 명시적으로 전부 활성화 (위와 동일)
agent = ClawOpsAgent(
    from_="07012341234",
    session=session,
    builtin_tools=BuiltinTool.ALL,
)

# 내장 도구 전부 비활성화 (커스텀 도구만 사용)
agent = ClawOpsAgent(
    from_="07012341234",
    session=session,
    builtin_tools=BuiltinTool.NONE,
)

# hang_up만 사용 (DTMF 도구 제외)
agent = ClawOpsAgent(
    from_="07012341234",
    session=session,
    builtin_tools=[BuiltinTool.HANG_UP],
)

# DTMF만 사용, hang_up 제외 (AI가 전화를 끊지 못하게)
agent = ClawOpsAgent(
    from_="07012341234",
    session=session,
    builtin_tools=[BuiltinTool.COLLECT_DTMF, BuiltinTool.SEND_DTMF],
)
```

### 활용 예시

**ARS 아웃바운드 봇** — 발신 후 ARS를 탐색해야 하므로 `SEND_DTMF`만 필요:

```python
agent = ClawOpsAgent(
    from_="07012341234",
    session=session,
    builtin_tools=[BuiltinTool.HANG_UP, BuiltinTool.SEND_DTMF],
)
```

**고객 인증 봇** — 고객이 주민번호 뒤 자리를 키패드로 입력:

```python
agent = ClawOpsAgent(
    from_="07012341234",
    session=session,
    builtin_tools=[BuiltinTool.HANG_UP, BuiltinTool.COLLECT_DTMF],
)
```

**고객센터 봇** — AI가 1차 응대 후 상담원으로 전환:

```python
agent = ClawOpsAgent(
    from_="07012341234",
    session=session,
    builtin_tools=[BuiltinTool.HANG_UP, BuiltinTool.TRANSFER_CALL],
)
```

---

## Call Transfer (통화 전환)

`transfer_call` 내장 도구를 활성화하면 AI가 대화 중 다른 번호로 통화를 전환할 수 있습니다.
코드에서 직접 `call.transfer()`를 호출할 수도 있습니다.

### Blind Transfer (즉시 전환)

고객을 대상 번호로 바로 연결합니다. 전환이 시작되면 AI 세션은 종료됩니다.

```python
await call.transfer("01012345678")
```

### Warm Transfer (안내 후 전환)

대상이 전화를 받으면 whisper 메시지를 먼저 들려준 후 고객과 연결합니다.
고객은 whisper를 들을 수 없으며, 연결 대기 중에는 대기 음악이 재생됩니다.

```python
await call.transfer(
    "01012345678",
    mode="warm",
    whisper="VIP 고객님이십니다. 주문 번호는 A1234입니다.",
)
```

### 전환 후 AI 복귀

전환된 통화가 끝나면 AI가 다시 고객과 대화를 이어갑니다.
예를 들어 전문 상담원과 통화 후 AI가 후속 안내를 하는 시나리오에 적합합니다.

```python
await call.transfer(
    "01012345678",
    after_transfer="return",  # 기본값: "terminate" (AI 세션 종료)
)
```

### SIP 엔드포인트 전환

전화번호(PSTN) 대신 SIP 엔드포인트로 통화를 직접 전환합니다.
PSTN 통신사를 거치지 않고 SIP URI(`sip:user@host`)로 INVITE 브릿지합니다 (녹음·관측 유지).

```python
await call.transfer(
    "sip:agent@sip.example.com",
    destination_type="sip",  # 기본값: "pstn" (전화번호 전환)
)
```

> **`sip_trunk` 부가서비스가 필요합니다.** 부가서비스가 없는 계정이 SIP 전환을 시도하면
> 전환은 실패하고(`{"status": "failed"}`) 통화는 끊기지 않은 채 AI가 계속 응대합니다.
> 인바운드 BYOC/softphone 라우팅과 동일한 게이트입니다.

### Context 전달

전환 대상에게 고객 정보 등 구조화 데이터를 webhook으로 전달할 수 있습니다.

```python
await call.transfer(
    "01012345678",
    mode="warm",
    whisper="VIP 고객입니다.",
    hold_media="moh",        # 고객 대기 음악 ("moh" 또는 "silence")
    context={
        "customer_name": "홍길동",
        "order_id": "ORD-20260325-001",
        "priority": "high",
    },
)
```

### 파라미터

| 파라미터 | 타입 | 기본값 | 설명 |
|:---------|:-----|:-------|:-----|
| `to` | `str` | (필수) | 전환 대상. `destination_type="pstn"`이면 전화번호, `"sip"`이면 SIP URI(`sip:user@host`) |
| `destination_type` | `str` | `"pstn"` | `"pstn"`: 통신사 경유 전화번호 전환, `"sip"`: SIP 엔드포인트 직접 전환 (**`sip_trunk` 부가서비스 필요**) |
| `mode` | `str` | `"blind"` | `"blind"`: 즉시 전환, `"warm"`: whisper 후 전환 |
| `after_transfer` | `str` | `"terminate"` | `"terminate"`: AI 세션 종료, `"return"`: 전환 통화 종료 후 AI가 다시 대화를 이어감 |
| `hold_media` | `str` | `"moh"` | 전환 중 고객에게 재생할 대기 음원. `"moh"`: 대기 음악, `"silence"`: 무음 |
| `whisper` | `str` | `None` | Warm 모드에서 대상이 전화를 받았을 때 전달할 안내 메시지 (TTS). 고객에게는 들리지 않음 |
| `context` | `dict` | `None` | 전환 대상에게 webhook으로 전달할 구조화 데이터 (예: 고객 정보, 주문 번호 등) |
| `caller_id` | `str` | `None` | 전환 발신 시 표시할 발신자 번호 오버라이드 |
| `timeout` | `int` | `30` | 대상 응답 대기 시간 (초). 초과 시 전환 실패 처리 |

**단순 안내 봇** — 통화 종료만 가능하면 충분:

```python
agent = ClawOpsAgent(
    from_="07012341234",
    session=session,
    builtin_tools=[BuiltinTool.HANG_UP],
)
```
