"""Shared UI-server harness for test_t276_ui and test_agent_scoped_chat (T-549)."""
from __future__ import annotations

import atexit
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

from session_adapters import AMBIENT_TRANSPORT_VARS, TRANSPORT_BOARD_ENV
from test_wakeup import run

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"
_SUPERVISOR = Path(__file__).resolve().parent / "_ui_supervisor.py"
_PID_FILE = Path(__file__).resolve().parent / ".ui_server_pids"

_ACTIVE_SERVERS: list["UiServer"] = []


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _board_marker(board, probe_prefix: str) -> str:
    marker = "%s-%s" % (probe_prefix, uuid.uuid4().hex[:12])
    run(board, "join", marker, agent=marker)
    return marker


def _wait_up(port: int, marker: str, timeout: float = 10) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/board.json" % port, timeout=1) as r:
                d = json.loads(r.read())
            agents = [a.get("name") for a in d.get("agents") or []]
            if marker in agents:
                return True
            raise RuntimeError(
                "foreign tickets ui on port %d: /board.json is up but missing probe agent '%s' (agents=%s)"
                % (port, marker, agents)
            )
        except RuntimeError:
            raise
        except (urllib.error.URLError, ConnectionError, json.JSONDecodeError, KeyError, TimeoutError, OSError):
            time.sleep(0.1)
    return False


def _read_pid_entries() -> list[tuple[int, int]]:
    if not _PID_FILE.exists():
        return []
    entries: list[tuple[int, int]] = []
    for line in _PID_FILE.read_text().splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        pid_s, port_s = line.split(":", 1)
        try:
            entries.append((int(pid_s), int(port_s)))
        except ValueError:
            continue
    return entries


def _write_pid_entries(entries: list[tuple[int, int]]) -> None:
    if not entries:
        _PID_FILE.unlink(missing_ok=True)
        return
    _PID_FILE.write_text("\n".join("%d:%d" % (pid, port) for pid, port in entries) + "\n")


def _register_ui_pid(pid: int, port: int) -> None:
    entries = _read_pid_entries()
    entries.append((pid, port))
    _write_pid_entries(entries)


def _unregister_ui_pid(pid: int) -> None:
    entries = [(p, port) for p, port in _read_pid_entries() if p != pid]
    _write_pid_entries(entries)


def _kill_pid_tree(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def leftover_suite_ui_children(pytest_pid: int | None = None) -> list[tuple[int, int, str]]:
    """ui --port processes still parented by this suite or already reparented to 1."""
    from watch_reaper import process_cmdline
    me = int(pytest_pid or os.getpid())
    leftover: list[tuple[int, int, str]] = []
    if os.path.isdir("/proc"):
        try:
            names = os.listdir("/proc")
        except OSError:
            names = []
        for name in names:
            if not name.isdigit():
                continue
            pid = int(name)
            if pid == me:
                continue
            cmd = process_cmdline(pid)
            if " ui " not in cmd or "--port" not in cmd:
                continue
            try:
                with open("/proc/%d/stat" % pid) as fh:
                    rest = fh.read().rsplit(")", 1)[-1].split()
                ppid = int(rest[1])
            except (OSError, IndexError, ValueError):
                continue
            if ppid == me:
                leftover.append((pid, ppid, cmd))
        return leftover
    env = os.environ.copy()
    env["COLUMNS"] = "65535"
    try:
        out = subprocess.check_output(
            ["ps", "-xww", "-o", "pid=,ppid=,args="], text=True, env=env)
    except OSError:
        return leftover
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        cmd = parts[2]
        if pid == me:
            continue
        if " ui " not in cmd or "--port" not in cmd:
            continue
        if ppid == me:
            leftover.append((pid, ppid, cmd))
    return leftover


def reap_stale_ui_servers() -> None:
    """Session-start: kill ui servers left by a killed pytest runner."""
    for pid, _port in _read_pid_entries():
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        _kill_pid_tree(pid)
    _PID_FILE.unlink(missing_ok=True)


def extract_ui_launch_token(html):
    """Read the per-launch write token the page embeds (T-1105)."""
    m = re.search(r'const UI_TOKEN="([^"]*)"', html or "")
    return m.group(1) if m else ""


# Same isolation run() builds: temp HOME/cache, no live seat or session.
# T-1104 scrubs the runner's seat identity, T-1107 its transport: the ui child
# can neither post as the runner nor wake the runner's own live session.
_SESSION_ENV = (
    "TICKET_SEAT", "TICKET_AGENT", "TICKET_SESSION_ID",
    "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID",
    "TERM_SESSION_ID", *AMBIENT_TRANSPORT_VARS, TRANSPORT_BOARD_ENV,
)


class UiServer:
    def __init__(self, board, probe_prefix: str = "ui-probe",
                 env: dict | None = None, operator: str = ""):
        self.board = board
        self._stopped = False
        self.marker = _board_marker(board, probe_prefix)
        self.port = _free_port()
        home = board.parent.parent / "home"
        cache = board.parent / "cache"
        cache.mkdir(exist_ok=True)
        home.mkdir(exist_ok=True)
        launch_env = dict(os.environ, TICKETS_DIR=str(board),
                          HOME=str(home), TICKETS_CACHE_DIR=str(cache))
        for var in _SESSION_ENV:
            launch_env.pop(var, None)
        if env:
            launch_env.update(env)
        ui_cmd = [sys.executable, str(TOOL), "ui", "--port", str(self.port),
                  "--host", "127.0.0.1", "--parent-pid", str(os.getpid())]
        if operator:
            ui_cmd += ["--operator", operator]
        self.proc = subprocess.Popen(
            [sys.executable, str(_SUPERVISOR), str(os.getpid())] + ui_cmd,
            env=launch_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _ACTIVE_SERVERS.append(self)
        _register_ui_pid(self.proc.pid, self.port)
        try:
            if not _wait_up(self.port, self.marker):
                raise RuntimeError("tickets ui never came up on port %d" % self.port)
        except Exception:
            self.stop()
            raise

    def get(self, path="/board.json", raw=False):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path), timeout=5) as r:
            body = r.read()
            return body if raw else json.loads(body)

    def launch_token(self):
        if getattr(self, "_launch_token", None) is None:
            self._launch_token = extract_ui_launch_token(
                self.get("/", raw=True).decode())
        return self._launch_token

    def post(self, path, payload, headers=None, token=True):
        head = {"Content-Type": "application/json"}
        if token is True:
            t = self.launch_token()
            if t:
                head["X-Atman-Token"] = t
        elif token:
            head["X-Atman-Token"] = token
        if headers:
            head.update(headers)
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path),
            data=json.dumps(payload).encode(),
            method="POST",
            headers=head,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        proc = getattr(self, "proc", None)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass
                proc.wait(timeout=2)
        if proc is not None:
            _unregister_ui_pid(proc.pid)
        try:
            _ACTIVE_SERVERS.remove(self)
        except ValueError:
            pass


def _stop_all_servers() -> None:
    for srv in list(_ACTIVE_SERVERS):
        srv.stop()


atexit.register(_stop_all_servers)


def stop_all_ui_servers() -> None:
    _stop_all_servers()
    reap_stale_ui_servers()


def port_is_dead(port: int, timeout: float = 3) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/board.json" % port, timeout=0.2)
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            return True
        time.sleep(0.05)
    return False


def make_ui_server_fixture(probe_prefix: str, operator: str = ""):
    @pytest.fixture
    def ui_server(board):
        srv = UiServer(board, probe_prefix=probe_prefix, operator=operator)
        try:
            yield srv
        finally:
            srv.stop()

    return ui_server


# Teeth-run bookkeeping for test_ui_server_fixture_finalizer_has_teeth.
FIXTURE_TEETH_PORT_FILE = Path(__file__).resolve().parent / ".fixture_teeth_port"
