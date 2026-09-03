"""DTMF 라우팅 및 패시브 DTMF 테스트."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_passive_dtmf_debounce():
    """패시브 DTMF가 debounce 후 feed_dtmf를 호출하는지 확인."""
    from clawops.agent._agent import ClawOpsAgent
    from clawops.agent.pipeline._base import Session

    # 최소 Session mock
    mock_session = MagicMock(spec=Session)
    mock_session.feed_dtmf = AsyncMock()

    agent = ClawOpsAgent(
        api_key="test_key",
        account_id="AC_test",
        from_="01012345678",
        session=mock_session,
        passive_dtmf_debounce_ms=100,
    )

    # 패시브 DTMF 테스트를 위한 mock call
    from clawops.agent._session import CallSession
    mock_call = CallSession(
        call_id="CA_test", from_number="010", to_number="070", account_id="AC",
    )
    agent._call_sessions["CA_test"] = mock_session
    agent._active_sessions["CA_test"] = mock_call

    agent._on_dtmf_event(mock_call, "1")
    agent._on_dtmf_event(mock_call, "2")
    agent._on_dtmf_event(mock_call, "3")

    # debounce 대기 (100ms + 여유)
    await asyncio.sleep(0.2)

    mock_session.feed_dtmf.assert_awaited_once_with("123")


@pytest.mark.asyncio
async def test_passive_dtmf_routing_to_collector():
    """collect_dtmf 활성 시 패시브 DTMF가 아닌 collector 큐로 라우팅."""
    from clawops.agent._session import CallSession

    session = CallSession(
        call_id="CA_test", from_number="010", to_number="070", account_id="AC",
    )

    async def collect():
        return await session.collect_dtmf(max_digits=3, timeout=2)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.05)

    # collector가 활성화된 상태에서 digit 라우팅
    session._route_dtmf("4")
    session._route_dtmf("5")
    session._route_dtmf("6")

    result = await task
    assert result == "456"


@pytest.mark.asyncio
async def test_중복_호출은_busy_예외로_구분된다():
    """모델이 도구를 두 번 내도 '고장'이 아니라 '기다리라'로 갈려야 한다.

    맨 RuntimeError 로 던지면 도구 래퍼가 "Error: ..." 로 감싸고, 모델은 도구가 망가진 줄
    알고 그 뒤로 다시 부르지 않는다 — 그때부터 발신자의 키는 아무도 받지 않는다.
    """
    from clawops.agent._session import CallSession, DtmfCollectorBusy

    session = CallSession(
        call_id="CA_test", from_number="010", to_number="070", account_id="AC",
    )

    task = asyncio.create_task(session.collect_dtmf(max_digits=3, timeout=1))
    await asyncio.sleep(0.05)

    with pytest.raises(DtmfCollectorBusy):
        await session.collect_dtmf(max_digits=3, timeout=1)

    # 기존에 RuntimeError 를 잡던 호출부가 깨지지 않아야 한다.
    assert issubclass(DtmfCollectorBusy, RuntimeError)

    session._route_dtmf("1")
    session._route_dtmf("2")
    session._route_dtmf("3")
    assert await task == "123"


@pytest.mark.asyncio
async def test_수집값을_로그에_쓰지_않는다(caplog):
    """키패드 값은 카드번호일 수 있다 — 자릿수만 남아야 한다."""
    import logging

    from clawops.agent._session import CallSession

    session = CallSession(
        call_id="CA_test", from_number="010", to_number="070", account_id="AC",
    )

    with caplog.at_level(logging.INFO, logger="clawops.agent"):
        task = asyncio.create_task(session.collect_dtmf(max_digits=4, timeout=1))
        await asyncio.sleep(0.05)
        for d in "4111":
            session._route_dtmf(d)
        assert await task == "4111"

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "4111" not in logged
    assert "4 digits" in logged


@pytest.mark.asyncio
async def test_전체_상한이_자리마다_리셋되는_타이머를_끊는다():
    """inter-digit 타이머만 있으면 max_digits × timeout 만큼 산다.

    11자리·5초면 55초 동안 모델이 이 도구에 붙들려 아무 말도 못 한다. max_wait 가
    그 최악을 끊고, 그때까지 모인 값으로 확정한다.
    """
    from clawops.agent._session import CallSession

    session = CallSession(
        call_id="CA_test", from_number="010", to_number="070", account_id="AC",
    )

    loop = asyncio.get_running_loop()
    started = loop.time()

    async def feed_slowly() -> None:
        # 자리 사이 간격(0.15초)은 timeout(10초) 안이라 inter-digit 만으로는 영영 안 끝난다.
        for d in "12":
            await asyncio.sleep(0.15)
            session._route_dtmf(d)

    asyncio.create_task(feed_slowly())
    result = await session.collect_dtmf(max_digits=11, timeout=10, max_wait=0.5)
    elapsed = loop.time() - started

    assert result == "12"
    assert elapsed < 2, f"전체 상한이 안 먹었다: {elapsed:.2f}s"
