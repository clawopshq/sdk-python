"""배포 환경 진단 — 조용히 실패하는 둘을 시끄럽게 만든다.

부모 체인 탐색은 /proc 을 읽으므로 여기서는 그 두 읽기 함수를 픽스처로 갈아끼운다.
실제 컨테이너 확인은 harness/ecs/Dockerfile.shell 로 따로 한다(하니스 README).
"""

import logging

import pytest

from clawops.agent import _deploy_checks as dc


def _chain(*cmdlines):
    """pid 1 부터 위로 쌓인 프로세스 체인을 픽스처로 만든다. 마지막이 우리 프로세스."""
    pids = list(range(1, len(cmdlines) + 1))
    by_pid = dict(zip(pids, cmdlines))
    parent = {pid: pid - 1 for pid in pids}  # pid 1 의 부모는 0(더 없음)
    return by_pid, parent, pids[-1]


@pytest.fixture
def fake_proc(monkeypatch):
    def install(*cmdlines):
        by_pid, parent, self_pid = _chain(*cmdlines)
        monkeypatch.setattr(dc.os, "getpid", lambda: self_pid)
        monkeypatch.setattr(dc, "_read_cmdline", lambda pid: by_pid[pid])
        monkeypatch.setattr(dc, "_read_ppid", lambda pid: parent[pid])
        monkeypatch.setattr(dc, "_in_container", lambda: True)

    return install


def test_exec_form_은_경고하지_않는다(fake_proc):
    # CMD ["python", "-u", "app.py"] → PID 1 이 곧 우리다.
    fake_proc(["python", "-u", "app.py"])
    assert dc.find_signal_swallower() is None


def test_셸_형태_CMD_는_잡힌다(fake_proc):
    # CMD python -u app.py → PID 1 = /bin/sh, 그 자식이 python.
    fake_proc(["/bin/sh", "-c", "python -u app.py"], ["python", "-u", "app.py"])
    assert dc.find_signal_swallower() == "sh"


def test_npm_start_는_잡힌다(fake_proc):
    # 실측상 /proc/1/cmdline 이 `npm\0start` 였다.
    fake_proc(["npm", "start"], ["node", "dist/index.js"])
    assert dc.find_signal_swallower() == "npm"


def test_npm_이_node_로_보이는_형태도_잡는다(fake_proc):
    # 셸·버전에 따라 argv[0] 이 node, argv[1] 이 npm-cli.js 로 나온다 — argv[1] 도 봐야 한다.
    fake_proc(["node", "/usr/lib/node_modules/npm/bin/npm-cli.js", "start"], ["node", "app.js"])
    assert dc.find_signal_swallower() == "npm-cli.js"


def test_tini_는_정상이라_경고하지_않는다(fake_proc):
    fake_proc(["/sbin/tini", "--"], ["python", "app.py"])
    assert dc.find_signal_swallower() is None


def test_셸이_tini_아래에_있어도_잡는다(fake_proc):
    # tini 가 있어도 그 아래 셸이 있으면 시그널은 셸에서 멎는다. 탐색은 아래에서
    # 위로 가므로 셸을 먼저 만난다.
    fake_proc(["/sbin/tini", "--"], ["/bin/sh", "-c", "python app.py"], ["python", "app.py"])
    assert dc.find_signal_swallower() == "sh"


def test_shareProcessNamespace_에서_pause_는_우리_트리가_아니다(fake_proc):
    # PID 1 이 /pause 다 — PID 1 만 보면 그 위의 셸을 통째로 놓친다.
    fake_proc(["/pause"], ["/bin/sh", "-c", "python app.py"], ["python", "app.py"])
    assert dc.find_signal_swallower() == "sh"


def test_컨테이너가_아니면_아예_검사하지_않는다(monkeypatch, caplog):
    # 로컬 터미널은 부모가 항상 셸이다 — 여기서 경고하면 매번 틀린 경고가 나가고,
    # 그러면 정작 진짜일 때 아무도 안 읽는다.
    monkeypatch.setattr(dc, "_in_container", lambda: False)
    monkeypatch.setattr(dc, "find_signal_swallower", lambda: "sh")
    with caplog.at_level(logging.WARNING, logger="clawops.agent"):
        dc.warn_if_signals_blocked()
    assert caplog.records == []


def test_proc_이_없으면_조용히_넘어간다(monkeypatch):
    def boom(_pid):
        raise FileNotFoundError("/proc 없음 (macOS)")

    monkeypatch.setattr(dc, "_read_cmdline", boom)
    assert dc.find_signal_swallower() is None


def test_경고에_고치는_법이_들어간다(fake_proc, caplog):
    fake_proc(["/bin/sh", "-c", "python app.py"], ["python", "app.py"])
    with caplog.at_level(logging.WARNING, logger="clawops.agent"):
        dc.warn_if_signals_blocked()
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "exec form" in message
    assert "tini" in message


class TestReadyMarker:
    def test_낡은_표시를_지우고_사실을_남긴다(self, tmp_path, monkeypatch):
        marker = tmp_path / "clawops-ready"
        marker.touch()
        monkeypatch.setenv("CLAWOPS_READY_FILE", str(marker))
        dc.clear_stale_ready_marker()
        assert not marker.exists()
        # import 시점엔 로깅 설정 전이라 경고가 묻힌다 — 사실만 담아 두고 나중에 알린다.
        assert dc.take_stale_clear_notice() == str(marker)
        assert dc.take_stale_clear_notice() is None, "한 번만 돌려줘야 한다"

    def test_없으면_조용하다(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAWOPS_READY_FILE", str(tmp_path / "nope"))
        dc.clear_stale_ready_marker()
        assert dc.take_stale_clear_notice() is None

    def test_빈_값이면_끈다(self, tmp_path, monkeypatch):
        marker = tmp_path / "clawops-ready"
        marker.touch()
        monkeypatch.setenv("CLAWOPS_READY_FILE", "")
        dc.clear_stale_ready_marker()
        assert marker.exists()

    def test_지울_수_없어도_기동을_막지_않는다(self, monkeypatch):
        monkeypatch.setenv("CLAWOPS_READY_FILE", "/proc/1/ready")  # 지울 수 없는 경로
        dc.clear_stale_ready_marker()  # 예외가 나가면 안 된다
