"""배포 환경에서 조용히 실패하는 것들을 시끄럽게 만든다.

여기 있는 검사는 **동작을 바꾸지 않는다.** 경고를 남기고 낡은 흔적을 지울 뿐이다.
둘 다 배포 지표에 안 잡히는 종류의 사고를 겨눈다.

1. **종료 시그널이 프로세스에 안 닿는 경우.** ``CMD python app.py`` 처럼 셸 형태로 쓰면
   PID 1 이 ``/bin/sh`` 가 되고, 셸은 SIGTERM 을 자식에게 전달하지 않는다. 컨테이너를
   내릴 때 SDK 는 종료를 듣지 못하고 유예가 끝날 때까지 새 콜을 계속 받다가 SIGKILL 로
   전부 끊긴다. 하니스 실측(2026-09-09): 45초 통화 기준 **26건이 잘렸는데 불통은 0** 이었다
   — 배달 지표만 보면 아무 문제가 없어 보인다.

2. **낡은 준비 표시.** 준비 표시 파일을 쓰는 배포에서, 컨테이너가 SIGKILL 로 죽거나
   재시작되면 파일이 남는다. ``/tmp`` 가 emptyDir 이면 pod 수명 동안 남으므로, 새
   프로세스는 **연결되기도 전에** Ready 로 판정되고 그동안 오는 콜이 죽는다.

검사는 전부 **실패해도 조용하다** — 진단이 기동을 막으면 안 된다.
"""

from __future__ import annotations

import logging
import os
import time
from typing import List, Optional

log = logging.getLogger("clawops.agent")

# 준비 표시 파일의 기본 경로. 배포 문서가 지시하는 그 경로다.
DEFAULT_READY_FILE = "/tmp/clawops-ready"

# 부모 체인에서 이걸 만나면 시그널이 안 닿는다. npm 은 신호를 전달하기는 하지만 버전과
# 플랫폼에 따라 다르고, 그 자체로 한 층을 더 얹으므로 함께 경고한다.
_SIGNAL_SWALLOWERS = {
    "sh", "bash", "dash", "ash", "ksh", "zsh", "busybox",
    "npm", "npm-cli.js", "yarn", "pnpm", "npx",
}

# 이걸 만나면 정상이다 — 시그널을 자식에게 제대로 전달하는 init 들. 여기서 탐색을 멈춘다.
_SIGNAL_FORWARDERS = {
    "tini", "dumb-init", "docker-init", "catatonit",
    "s6-svscan", "s6-supervise", "supervisord", "runsvdir", "runit",
}

# 컨테이너 런타임이 만드는 것들 — 우리 프로세스 트리의 조상이 아니다.
_NOT_OUR_TREE = {"pause", "systemd", "launchd", "init"}

_MAX_CHAIN = 12


def _read_cmdline(pid: int) -> List[str]:
    with open("/proc/%d/cmdline" % pid, "rb") as fh:
        raw = fh.read()
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def _read_ppid(pid: int) -> int:
    # /proc/<pid>/stat 은 "pid (comm) state ppid ..." 인데 comm 에 공백과 괄호가 들어갈 수
    # 있다. 그래서 **마지막** ')' 뒤부터 자른다 — split() 로 앞에서 세면 틀린다.
    with open("/proc/%d/stat" % pid, "r") as fh:
        text = fh.read()
    tail = text[text.rindex(")") + 2:].split()
    return int(tail[1])


def _names(cmdline: List[str]) -> List[str]:
    # argv[0] 과 argv[1] 의 basename 을 둘 다 본다. `CMD ["npm","start"]` 는 실측상
    # /proc/1/cmdline 이 `npm\0start` 였지만, 셸·버전에 따라 `node\0.../npm-cli.js` 로도
    # 나온다 — 한쪽만 보면 그중 하나를 놓친다.
    return [os.path.basename(arg) for arg in cmdline[:2]]


def _in_container() -> bool:
    # 컨테이너가 아니면 검사하지 않는다. 로컬 터미널에서는 부모가 항상 셸이라, 여기서
    # 경고를 내면 **매번 틀린 경고**가 나가고 그러면 아무도 경고를 안 읽게 된다.
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return True
    if os.environ.get("ECS_CONTAINER_METADATA_URI_V4") or os.environ.get("ECS_CONTAINER_METADATA_URI"):
        return True
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "r") as fh:
            cgroup = fh.read()
    except OSError:
        return False
    return any(marker in cgroup for marker in ("docker", "kubepods", "containerd", "ecs", "lxc"))


def find_signal_swallower() -> Optional[str]:
    """종료 시그널을 삼킬 조상이 있으면 그 이름을 돌려준다.

    자기 자신부터 부모를 따라 올라간다. PID 1 만 보면 ``shareProcessNamespace: true`` 에서
    놓친다 — 그때 PID 1 은 ``/pause`` 이고 우리 컨테이너의 진입점은 다른 pid 다.
    """
    try:
        pid = os.getpid()
        for _ in range(_MAX_CHAIN):
            names = _names(_read_cmdline(pid))
            if any(name in _SIGNAL_FORWARDERS for name in names):
                return None  # init 이 전달해 준다 — 여기서 멈춘다
            if any(name in _NOT_OUR_TREE for name in names):
                return None  # 우리 트리 밖(런타임의 PID 1)
            if pid != os.getpid():
                # 걸린 이름 자체를 돌려준다 — argv[0] 을 돌려주면 `node npm-cli.js` 를
                # "node 가 문제" 로 보고하게 되어 고치는 사람이 엉뚱한 데를 본다.
                for name in names:
                    if name in _SIGNAL_SWALLOWERS:
                        return name
            ppid = _read_ppid(pid)
            if ppid <= 0 or ppid == pid:
                return None
            pid = ppid
    except (OSError, ValueError, IndexError):
        return None  # /proc 이 없거나(macOS) 형식이 다르다 — 조용히 넘어간다
    return None


def warn_if_signals_blocked() -> None:
    """시그널이 안 닿는 구성이면 경고 한 번. 동작은 바꾸지 않는다."""
    if not _in_container():
        return
    swallower = find_signal_swallower()
    if not swallower:
        return
    log.warning(
        "종료 시그널이 이 프로세스에 닿지 않는다 — 부모 프로세스가 '%s' 다. "
        "셸과 npm 은 SIGTERM 을 자식에게 전달하지 않는다. 배포로 컨테이너를 내릴 때 "
        "이 프로세스는 종료를 듣지 못하고, 유예가 끝날 때까지 새 콜을 계속 받다가 "
        "SIGKILL 로 진행 중 통화가 통째로 끊긴다(불통 지표에는 안 잡힌다). "
        "고치기: Dockerfile 의 CMD 를 exec form 으로 두거나(CMD [\"python\", \"-u\", \"app.py\"]) "
        "tini/dumb-init 를 ENTRYPOINT 로 둘 것. "
        "https://platform.claw-ops.com/docs/sdk/python/agent/deployment",
        swallower,
    )


def write_ready_marker() -> None:
    """이제 콜을 받을 수 있다고 표시한다.

    **이 시점을 아는 것은 SDK 뿐이다.** 컨테이너가 떴다는 것과 콜을 받을 수 있다는 것은
    다르다 — 그 사이(무거운 import, 모델 클라이언트 초기화, control 연결)가 배포의 빈틈이
    되는 구간이고, 오케스트레이터는 그 끝을 알 방법이 없다. 그래서 예전에는 고객 앱이
    ``connect()`` 뒤에 직접 파일을 만들어야 했다. 그 한 줄을 없애는 것이 이 함수다.

    내용에 pid 와 기동 시각을 적는다 — 낡은 마커를 만났을 때 누가 남긴 것인지 보인다.
    """
    path = os.environ.get("CLAWOPS_READY_FILE", DEFAULT_READY_FILE)
    if not path:
        return
    try:
        with open(path, "w") as fh:
            fh.write(f"pid={os.getpid()} since={int(time.time())}\n")
    except OSError as err:
        # read-only rootfs 등 — 표시를 못 남기는 것이 기동을 막을 이유는 아니다.
        # 다만 조용히 넘기면 프로브가 영영 안 붙는데 아무도 모른다.
        log.warning(
            "준비 표시를 남기지 못했다 (%s): %s — readinessProbe 를 쓰고 있다면 그 프로브는 "
            "영영 통과하지 못한다. 쓰기 가능한 볼륨(emptyDir 등)을 마운트하거나 "
            "CLAWOPS_READY_FILE 로 경로를 옮길 것.",
            path,
            err,
        )


def remove_ready_marker() -> None:
    """더 이상 새 콜을 받지 않는다고 표시한다(자리 인계·드레이닝·종료).

    이걸 지우는 것이 프로브를 떨어뜨려 오케스트레이터가 이 인스턴스를 뒤로 뺀다.
    """
    path = os.environ.get("CLAWOPS_READY_FILE", DEFAULT_READY_FILE)
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError as err:
        log.debug("준비 표시 삭제 실패 (%s): %s", path, err)


def clear_stale_ready_marker() -> None:
    """기동 시 낡은 준비 표시를 지운다.

    지우는 시점이 중요하다 — 애플리케이션이 표시를 만들기 **전**, 즉 연결을 시작하는
    순간이어야 한다. 그 뒤에 지우면 방금 만든 표시를 지운다.

    ``CLAWOPS_READY_FILE`` 로 경로를 바꾸고, 빈 값이면 끈다.
    """
    path = os.environ.get("CLAWOPS_READY_FILE", DEFAULT_READY_FILE)
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError as err:
        # read-only rootfs 등 — 진단이 기동을 막으면 안 된다.
        log.debug("준비 표시 정리 실패 (%s): %s", path, err)
        return
    log.warning(
        "낡은 준비 표시를 지웠다: %s — 이전 프로세스가 남긴 것이다. "
        "이게 남아 있으면 새 프로세스가 연결되기도 전에 Ready 로 판정돼 그동안 오는 콜이 죽는다.",
        path,
    )
