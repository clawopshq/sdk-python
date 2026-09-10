"""준비 표시를 SDK 가 쓴다 — 고객 앱에서 그 한 줄이 사라진다.

컨테이너가 떴다는 것과 콜을 받을 수 있다는 것은 다르다. 그 사이(무거운 import, 모델
클라이언트 초기화, control 연결)가 배포의 빈틈이 되는 구간이고, **그 끝을 아는 것은 SDK 뿐**
이다. 그래서 예전에는 고객 앱이 `connect()` 뒤에 직접 파일을 만들어야 했다.

여기서 고정하는 것은 셋이다.
  1. 준비되면 생기고, 물러나면(인계·드레이닝·종료) **지워진다**
  2. 못 써도 기동을 막지 않는다 — 다만 조용하지는 않다
  3. distroless 를 위한 `/healthz` 가 같은 값을 본다
"""
from __future__ import annotations

import asyncio
import os

import pytest

from clawops.agent import _deploy_checks as dc
from clawops.agent._health import start_health_server


@pytest.fixture
def marker(tmp_path, monkeypatch):
    path = tmp_path / "clawops-ready"
    monkeypatch.setenv("CLAWOPS_READY_FILE", str(path))
    return path


class TestMarkerLifecycle:
    def test_준비되면_생긴다(self, marker):
        dc.write_ready_marker()
        assert marker.exists()

    def test_내용에_pid_가_있다(self, marker):
        # 낡은 마커를 만났을 때 누가 남긴 것인지 보여야 한다.
        dc.write_ready_marker()
        assert f"pid={os.getpid()}" in marker.read_text()

    def test_물러나면_지워진다(self, marker):
        dc.write_ready_marker()
        dc.remove_ready_marker()
        assert not marker.exists()

    def test_삭제는_멱등하다(self, marker):
        # 인계에서 한 번, 드레이닝에서 또 한 번 불린다.
        dc.remove_ready_marker()
        dc.remove_ready_marker()

    def test_빈_값이면_아무것도_안_한다(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWOPS_READY_FILE", "")
        dc.write_ready_marker()
        assert list(tmp_path.iterdir()) == []

    def test_못_써도_기동을_막지_않되_조용하지도_않다(self, monkeypatch, caplog):
        import logging

        monkeypatch.setenv("CLAWOPS_READY_FILE", "/proc/1/nope/ready")
        with caplog.at_level(logging.WARNING, logger="clawops.agent"):
            dc.write_ready_marker()  # 예외가 나가면 안 된다
        # 조용히 넘어가면 프로브가 영영 안 붙는데 아무도 모른다.
        assert "readinessProbe" in caplog.text


class TestHealthEndpoint:
    """distroless 에는 cat 도 sh 도 없어 exec 프로브가 성립하지 않는다."""

    @pytest.mark.asyncio
    async def test_준비_전에는_503_준비되면_200(self) -> None:
        ready = False
        server = await start_health_server(0, lambda: ready)
        port = server.sockets[0].getsockname()[1]
        try:
            assert (await _probe(port)).startswith("HTTP/1.1 503")
            ready = True
            assert (await _probe(port)).startswith("HTTP/1.1 200")
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_경로를_안_가린다(self) -> None:
        # 프로브 경로 오타로 배포가 멈추는 쪽이 404 를 안 주는 것보다 나쁘다.
        server = await start_health_server(0, lambda: True)
        port = server.sockets[0].getsockname()[1]
        try:
            assert (await _probe(port, path="/healthz")).startswith("HTTP/1.1 200")
            assert (await _probe(port, path="/health")).startswith("HTTP/1.1 200")
        finally:
            server.close()
            await server.wait_closed()


async def _probe(port: int, path: str = "/healthz") -> str:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    await writer.drain()
    data = await asyncio.wait_for(reader.read(200), timeout=3)
    writer.close()
    await writer.wait_closed()
    return data.decode()
