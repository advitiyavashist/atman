"""T-960: atm ui must not outlive its pytest parent or a deleted board."""
from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from test_wakeup import board, run  # noqa: F401
from ui_server_harness import leftover_suite_ui_children

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_up(port, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/board.json" % port, timeout=0.5)
            return True
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            time.sleep(0.1)
    return False


def _start_ui(board, port, extra=None):
    env = dict(os.environ, TICKETS_DIR=str(board))
    cmd = [sys.executable, str(TOOL), "ui", "--port", str(port), "--host", "127.0.0.1"]
    cmd.extend(extra or [])
    return subprocess.Popen(
        cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True,
    )


def _stop_group(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
        proc.wait(timeout=2)


def test_ui_exits_when_board_dir_disappears(board):
    run(board, "join", "ui-leak", agent="ui-leak")
    port = _free_port()
    proc = _start_ui(board, port)
    try:
        assert _wait_up(port), "ui never came up"
        shutil.rmtree(board)
        deadline = time.time() + 5
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.1)
        assert proc.poll() is not None, "ui stayed up after board directory vanished"
    finally:
        _stop_group(proc)


def test_ui_exits_when_parent_pid_dies(board):
    run(board, "join", "ui-leak", agent="ui-leak")
    port = _free_port()
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    proc = _start_ui(board, port, extra=["--parent-pid", str(parent.pid)])
    try:
        assert _wait_up(port)
        parent.kill()
        parent.wait(timeout=3)
        deadline = time.time() + 5
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.1)
        assert proc.poll() is not None, "ui stayed up after --parent-pid died"
    finally:
        _stop_group(proc)
        if parent.poll() is None:
            parent.kill()


def test_ui_process_group_teardown_kills_orphan(board):
    run(board, "join", "ui-leak", agent="ui-leak")
    port = _free_port()
    proc = _start_ui(board, port, extra=["--parent-pid", str(os.getpid())])
    try:
        assert _wait_up(port)
        _stop_group(proc)
        assert proc.poll() is not None
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/board.json" % port, timeout=0.4)
            alive = True
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            alive = False
        assert alive is False
    finally:
        _stop_group(proc)


def test_leftover_suite_ui_children_helper_sees_live_ui(board):
    run(board, "join", "ui-leak", agent="ui-leak")
    port = _free_port()
    proc = _start_ui(board, port)
    try:
        assert _wait_up(port)
        found = leftover_suite_ui_children(os.getpid())
        assert any(pid == proc.pid for pid, _ppid, _cmd in found)
    finally:
        _stop_group(proc)
        assert leftover_suite_ui_children(os.getpid()) == []
