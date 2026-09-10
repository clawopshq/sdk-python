"""`serve()` 의 종료 상태 기계 — 배포의 빈틈과 절단이 여기서 갈린다.

예전에는 SIGTERM 을 받는 즉시 자리를 놓았다. 그때 후임이 아직 안 떠 있으면 그 사이 오는
전화가 전부 죽었다 — readinessProbe 는 그 순간이 오지 않게 오케스트레이터를 붙잡아 두는
우회였다. 이제 두 국면을 지난다.

    SIGTERM ─▶ [인계 대기] 자리를 들고 계속 받는다 ─▶ [드레이닝] 진행 중 통화만 기다린다
                    └ agent.retired · 상한 · 마감 중 먼저 오는 것

고정해야 하는 것이 셋이다.
  1. 인계를 받았으면 **자리를 또 놓지 않는다**(연결 유지 → 종료 이벤트가 돌아온다)
  2. 시간은 **절대 마감 하나**를 나눠 쓴다(더하지 않는다 — 더하면 플랫폼 유예를 넘긴다)
  3. SIGINT 는 예전 그대로 — 로컬 Ctrl-C 가 20초를 기다리면 안 된다
"""
from __future__ import annotations

import asyncio
import os
import signal

import pytest

from clawops.agent._agent import ClawOpsAgent


class _FakeControlWs:
    def __init__(self) -> None:
        self.lame_duck = False

    def enter_lame_duck(self) -> None:
        self.lame_duck = True

    async def close(self) -> None:
        return None


class _FakeSession:
    async def start(self, *_a, **_k) -> None: ...
    async def stop(self, *_a, **_k) -> None: ...


def _agent() -> ClawOpsAgent:
    agent = ClawOpsAgent(
        api_key="sk_test",
        account_id="AC1",
        from_="07012345678",
        session=_FakeSession(),  # type: ignore[arg-type]
    )
    agent._control_ws = _FakeControlWs()  # type: ignore[assignment]
    return agent


def _instrument(agent: ClawOpsAgent) -> dict:
    """connect/drain/disconnect 를 갈아끼우고 drain 이 어떻게 불렸는지 기록한다."""
    seen: dict = {"drain": None, "disconnect": 0}

    async def _connect() -> None:
        return None

    async def _drain(*, timeout: float, release_slot: bool = True) -> tuple[int, int]:
        seen["drain"] = {"timeout": timeout, "release_slot": release_slot}
        return (0, 0)

    async def _disconnect() -> None:
        seen["disconnect"] += 1

    agent.connect = _connect  # type: ignore[method-assign]
    agent.drain = _drain  # type: ignore[method-assign]
    agent.disconnect = _disconnect  # type: ignore[method-assign]
    return seen


async def _raise_signal(sig: int, after: float = 0.05) -> None:
    await asyncio.sleep(after)
    os.kill(os.getpid(), sig)


@pytest.mark.asyncio
async def test_인계를_받으면_자리를_또_놓지_않는다() -> None:
    """release_slot=False. 연결을 유지해야 진행 중 통화의 종료 이벤트가 돌아온다."""
    agent = _agent()
    seen = _instrument(agent)

    async def _hand_over() -> None:
        await asyncio.sleep(0.15)
        agent._handle_retired("replaced")

    task = asyncio.create_task(agent.serve(handover_wait=5, shutdown_deadline=10))
    await asyncio.sleep(0.02)
    asyncio.create_task(_raise_signal(signal.SIGTERM, 0.02))
    asyncio.create_task(_hand_over())
    await asyncio.wait_for(task, timeout=5)

    assert seen["drain"]["release_slot"] is False
    assert agent._control_ws.lame_duck is True, "인계 대기 중에는 재연결하면 안 된다"


@pytest.mark.asyncio
async def test_인계가_안_오면_상한까지만_기다리고_자리를_놓는다() -> None:
    agent = _agent()
    seen = _instrument(agent)

    task = asyncio.create_task(agent.serve(handover_wait=0.3, shutdown_deadline=10))
    await asyncio.sleep(0.02)
    asyncio.create_task(_raise_signal(signal.SIGTERM, 0.02))
    loop = asyncio.get_running_loop()
    started = loop.time()
    await asyncio.wait_for(task, timeout=5)
    elapsed = loop.time() - started

    assert seen["drain"]["release_slot"] is True, "인계가 없으면 자리를 우리가 놓아야 한다"
    assert 0.25 <= elapsed <= 2.0, f"인계 상한을 안 지켰다: {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_시간은_절대_마감_하나를_나눠_쓴다() -> None:
    """인계 대기가 쓴 만큼 드레이닝의 몫이 줄어든다 — 두 값을 더하면 플랫폼 유예를 넘는다."""
    agent = _agent()
    seen = _instrument(agent)

    task = asyncio.create_task(agent.serve(handover_wait=0.4, shutdown_deadline=3.0))
    await asyncio.sleep(0.02)
    asyncio.create_task(_raise_signal(signal.SIGTERM, 0.02))
    await asyncio.wait_for(task, timeout=5)

    # 마감 3초 중 인계 대기가 0.4초를 썼으니 드레이닝은 2.6초쯤 남아야 한다.
    assert 2.3 <= seen["drain"]["timeout"] <= 2.7, seen["drain"]["timeout"]


@pytest.mark.asyncio
async def test_드레이닝_상한이_마감보다_짧으면_그쪽이_이긴다() -> None:
    agent = _agent()
    seen = _instrument(agent)

    task = asyncio.create_task(
        agent.serve(drain_timeout=0.5, handover_wait=0.2, shutdown_deadline=30)
    )
    await asyncio.sleep(0.02)
    asyncio.create_task(_raise_signal(signal.SIGTERM, 0.02))
    await asyncio.wait_for(task, timeout=5)

    assert seen["drain"]["timeout"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_두번째_SIGTERM_은_인계_대기를_끊는다() -> None:
    agent = _agent()
    seen = _instrument(agent)

    task = asyncio.create_task(agent.serve(handover_wait=30, shutdown_deadline=60))
    await asyncio.sleep(0.02)
    asyncio.create_task(_raise_signal(signal.SIGTERM, 0.02))
    asyncio.create_task(_raise_signal(signal.SIGTERM, 0.15))
    loop = asyncio.get_running_loop()
    started = loop.time()
    await asyncio.wait_for(task, timeout=5)

    assert loop.time() - started < 2.0, "두 번째 시그널에도 30초를 기다렸다"
    # 절단이 아니라 **드레이닝으로 넘어간다** — 진행 중 통화는 여전히 지킨다.
    assert seen["drain"] is not None
    assert seen["disconnect"] == 0


@pytest.mark.asyncio
async def test_SIGINT_은_인계를_기다리지_않는다() -> None:
    """로컬 Ctrl-C 가 20초를 기다리면 안 된다."""
    agent = _agent()
    seen = _instrument(agent)

    task = asyncio.create_task(agent.serve(handover_wait=30, shutdown_deadline=60))
    await asyncio.sleep(0.02)
    asyncio.create_task(_raise_signal(signal.SIGINT, 0.02))
    loop = asyncio.get_running_loop()
    started = loop.time()
    await asyncio.wait_for(task, timeout=5)

    assert loop.time() - started < 1.0
    assert seen["drain"]["release_slot"] is True
    assert agent._control_ws.lame_duck is False, "SIGINT 에는 인계 대기가 없다"


@pytest.mark.asyncio
async def test_handover_wait_0_이면_예전_동작_그대로() -> None:
    """후임을 띄우지 않고 내리기만 하는 배포를 위한 탈출구."""
    agent = _agent()
    seen = _instrument(agent)

    task = asyncio.create_task(agent.serve(handover_wait=0, shutdown_deadline=30))
    await asyncio.sleep(0.02)
    asyncio.create_task(_raise_signal(signal.SIGTERM, 0.02))
    loop = asyncio.get_running_loop()
    started = loop.time()
    await asyncio.wait_for(task, timeout=5)

    assert loop.time() - started < 1.0
    assert seen["drain"]["release_slot"] is True
    assert agent._control_ws.lame_duck is False


@pytest.mark.asyncio
async def test_시그널_없이_온_인계는_그대로_반환한다() -> None:
    """오늘의 인계와 같다 — replicas 오설정 의심 로그만 더 남는다."""
    agent = _agent()
    seen = _instrument(agent)

    task = asyncio.create_task(agent.serve(handover_wait=30, shutdown_deadline=60))
    await asyncio.sleep(0.05)
    agent._handle_retired("replaced")
    await asyncio.wait_for(task, timeout=5)

    assert seen["drain"]["release_slot"] is False
    assert agent.taken_over is True
