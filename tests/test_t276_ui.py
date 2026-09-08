"""T-276 command-board UI + T-221 composer: /msg posts through post_message(),
same messages.jsonl, no second store. Snapshot keys stay stable."""
import atexit
import json
import os
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

from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"

SNAPSHOT_KEYS = (
    "project", "generated", "master", "cos", "counts", "sprint", "burn",
    "goals", "util", "in_flight", "review", "open", "agents", "health", "messages",
)

_ACTIVE_SERVERS: list["_Server"] = []


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _board_marker(board):
    marker = "t276-probe-%s" % uuid.uuid4().hex[:12]
    run(board, "join", marker, agent=marker)
    return marker


def _wait_up(port, marker, timeout=10):
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


class _Server:
    def __init__(self, board):
        self.board = board
        self._stopped = False
        self.marker = _board_marker(board)
        self.port = _free_port()
        env = dict(os.environ, TICKETS_DIR=str(board))
        self.proc = subprocess.Popen(
            [sys.executable, str(TOOL), "ui", "--port", str(self.port), "--host", "127.0.0.1"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        _ACTIVE_SERVERS.append(self)
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

    def post(self, path, payload):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path),
            data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        proc = getattr(self, "proc", None)
        if proc is None or proc.poll() is not None:
            try:
                _ACTIVE_SERVERS.remove(self)
            except ValueError:
                pass
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
            proc.wait(timeout=2)
        try:
            _ACTIVE_SERVERS.remove(self)
        except ValueError:
            pass


def _stop_all_servers():
    for srv in list(_ACTIVE_SERVERS):
        srv.stop()


atexit.register(_stop_all_servers)


def pytest_sessionfinish(session, exitstatus):
    _stop_all_servers()


@pytest.fixture(autouse=True)
def _reap_leftover_ui_servers():
    yield
    _stop_all_servers()


@pytest.fixture
def ui_server(board):
    srv = _Server(board)
    try:
        yield srv
    finally:
        srv.stop()


def test_ui_html_is_command_board_not_spreadsheet(board):
    html = TOOL.read_text()
    start = html.index('UI_HTML = r"""')
    ui = html[start:html.index('"""', start + 14)]
    assert "--bg:#0c0e12" in ui
    assert 'data-tab-btn="board"' in ui
    assert 'data-tab-btn="agents"' in ui
    assert 'data-tab-btn="messages"' in ui
    assert 'id="col-blocked"' in ui and 'id="col-ready"' in ui
    assert 'id="col-flight"' in ui and 'id="col-review"' in ui
    assert 'id="missionOne"' in ui
    assert 'id="mentionBar"' in ui and 'id="cSend"' in ui
    assert "fetch('/msg'" in ui
    assert "setInterval(load,5000)" in ui
    assert 'id="open"' not in ui  # old spreadsheet dump is gone


def test_composer_post_lands_in_the_same_board_and_reaches_mentioned_agent(board, ui_server):
    run(board, "join", "cursor", agent="cursor")
    run(board, "join", "alice", agent="alice")
    status, out = ui_server.post("/msg", {"from": "alice", "text": "@cursor check the composer", "to": ""})
    assert status == 200 and out["ok"], out
    assert "cursor" in out["posted"]
    page = ui_server.get("/", raw=True).decode()
    assert 'data-tab-btn="board"' in page
    assert 'id="col-blocked"' in page

    live = (board / "messages.jsonl").read_text()
    assert "check the composer" in live
    out = run(board, "inbox", agent="cursor").stdout
    assert "check the composer" in out


def test_composer_rejects_missing_fields_without_touching_the_board(board, ui_server):
    status, out = ui_server.post("/msg", {"from": "", "text": "no sender"})
    assert status == 400 and not out["ok"]
    status, out = ui_server.post("/msg", {"from": "alice", "text": ""})
    assert status == 400 and not out["ok"]
    assert not (board / "messages.jsonl").exists() or "no sender" not in (board / "messages.jsonl").read_text()


def test_board_json_keeps_snapshot_keys_and_feeds_mentions(board, ui_server):
    run(board, "join", "cursor", agent="cursor")
    run(board, "msg", "@cursor look at this", agent="alice")
    d = ui_server.get()
    for k in SNAPSHOT_KEYS:
        assert k in d, k
    assert any(a["name"] == "cursor" for a in d["agents"])
    assert any(m["from"] == "alice" and "cursor" in (m.get("mentions") or []) for m in d["messages"])


def test_wait_up_rejects_foreign_server_on_same_port(board, monkeypatch):
    """Mutation (b): a stray ui on our port must fail at _wait_up, not at content asserts."""
    foreign_repo = board.parent.parent / "foreign-repo"
    foreign_repo.mkdir()
    subprocess.run(["git", "init", "-q", str(foreign_repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(foreign_repo), "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    fb = foreign_repo / ".tickets"
    run(fb, "create", "foreign", "--role", "backend", cwd=foreign_repo)
    port = _free_port()
    stray = subprocess.Popen(
        [sys.executable, str(TOOL), "ui", "--port", str(port), "--host", "127.0.0.1"],
        env=dict(os.environ, TICKETS_DIR=str(fb)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/board.json" % port, timeout=1)
                break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.1)
        marker = _board_marker(board)
        monkeypatch.setattr("test_t276_ui._free_port", lambda: port)
        with pytest.raises(RuntimeError, match="foreign tickets ui"):
            _Server(board)
    finally:
        try:
            os.killpg(os.getpgid(stray.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            stray.terminate()
        stray.wait(timeout=5)


def test_fixture_stops_server_after_assertion_failure(board):
    """Covers the no-teardown gap: fixture finalizer must stop ui even if the body raises."""
    srv = None
    port = None
    try:
        srv = _Server(board)
        port = srv.port
        assert srv.get() is not None
        raise AssertionError("simulated test failure")
    except AssertionError:
        pass
    finally:
        if srv is not None:
            srv.stop()
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/board.json" % port, timeout=0.2)
        except (urllib.error.URLError, ConnectionError):
            return
        time.sleep(0.05)
    raise AssertionError("port %d still serving after stop()" % port)
