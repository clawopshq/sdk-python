"""``/healthz`` — 파일 마커를 못 쓰는 이미지를 위한 두 번째 준비 표시.

기본은 파일이다(``CLAWOPS_READY_FILE``). 그게 안 되는 경우가 하나 있다: **distroless 처럼
``cat`` 도 ``sh`` 도 없는 이미지**. 쿠버네티스의 exec 프로브는 컨테이너 안에서 명령을
실행하는 것이라 실행할 바이너리가 없으면 프로브 자체가 성립하지 않는다. 그때는 HTTP 프로브
뿐이고, HTTP 프로브를 받으려면 우리가 포트를 열어야 한다.

일부러 작게 둔다 — 이건 관측용 엔드포인트가 아니라 **준비/비준비 한 비트**다.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("clawops.agent")

_OK = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nContent-Type: text/plain\r\n\r\nready"
_NOT_READY = (
    b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 9\r\n"
    b"Content-Type: text/plain\r\n\r\nnot ready"
)


async def start_health_server(port: int, is_ready) -> asyncio.AbstractServer:
    """``/healthz`` 를 여는 최소 HTTP 서버. 준비면 200, 아니면 503.

    aiohttp 를 쓰지 않는다 — 프로브 하나 받자고 프레임워크를 들이면, 그 프레임워크가 죽을 때
    프로브도 같이 죽는다. asyncio 소켓 한 겹이면 그럴 일이 없다.

    Args:
        port: 열 포트. 0 이면 임의 포트(테스트용).
        is_ready: 지금 콜을 받을 수 있는지 돌려주는 callable.
    """

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            # 요청 줄만 읽고 나머지는 무시한다. 경로도 안 본다 — 이 서버에는 길이 하나뿐이라
            # 404 를 돌려줄 이유가 없고, 프로브 경로 오타로 배포가 멈추는 쪽이 더 나쁘다.
            await asyncio.wait_for(reader.readline(), timeout=2.0)
            writer.write(_OK if is_ready() else _NOT_READY)
            await writer.drain()
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    server = await asyncio.start_server(handle, "0.0.0.0", port)
    bound = server.sockets[0].getsockname()[1] if server.sockets else port
    log.info(f"Health 엔드포인트: http://0.0.0.0:{bound}/healthz")
    return server
