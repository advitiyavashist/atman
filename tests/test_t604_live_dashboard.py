"""T-604: live leadership, connection state, epics, limits, message delivery."""
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from test_wakeup import board, run  # noqa: F401
from ui_server_harness import extract_ui_launch_token

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_up(port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/board.json" % port, timeout=1)
            return True
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.15)
    return False


class _Server:
    def __init__(self, board, port, operator=""):
        self.port = port
        env = dict(os.environ, TICKETS_DIR=str(board))
        cmd = [sys.executable, str(TOOL), "ui", "--port", str(port), "--host", "127.0.0.1",
               "--parent-pid", str(os.getpid())]
        if operator:
            cmd += ["--operator", operator]
        self.proc = subprocess.Popen(
            cmd,
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if not _wait_up(port):
            self.stop()
            raise RuntimeError("tickets ui never came up on %d" % port)

    def get(self, path="/board.json"):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path), timeout=5) as r:
            return json.loads(r.read())

    def launch_token(self):
        if getattr(self, "_launch_token", None) is None:
            with urllib.request.urlopen("http://127.0.0.1:%d/" % self.port, timeout=5) as r:
                self._launch_token = extract_ui_launch_token(r.read().decode())
        return self._launch_token

    def post(self, path, payload):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path),
            data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "X-Atman-Token": self.launch_token()},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())

    def stop(self):
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                self.proc.kill()
            self.proc.wait(timeout=2)


def test_ui_has_live_connection_chrome():
    html = TOOL.read_text()
    start = html.index('UI_HTML = r"""')
    ui = html[start:html.index('"""', start + 14)]
    assert 'id="connStatus"' in ui
    assert 'id="lastUpdated"' in ui
    assert 'id="refreshBtn"' in ui
    assert 'id="themeBtn"' in ui
    assert 'id="attentionBox"' in ui
    assert 'id="epicsPanel"' in ui
    assert 'id="cKind"' in ui
    assert 'function setConn(' in ui


def test_leadership_change_propagates_without_restart(board):
    run(board, "join", "alpha", agent="alpha")
    run(board, "join", "beta", agent="beta")
    run(board, "master", "take", agent="alpha")
    srv = _Server(board, _free_port())
    try:
        d1 = srv.get()
        assert d1["master"] == "alpha"
        run(board, "master", "take", agent="beta")
        run(board, "master", "cos", "gamma", agent="beta")
        time.sleep(0.05)
        d2 = srv.get()
        assert d2["master"] == "beta"
        assert d2["cos"] == "gamma"
    finally:
        srv.stop()


def test_snapshot_includes_epics_attention_limits_and_delivery(board):
    run(board, "join", "sender", agent="sender")
    run(board, "join", "target", agent="target")
    run(board, "epic", "create", "Launch track")
    eid = run(board, "epic", "list").stdout.strip().split()[0]
    run(board, "create", "Epic child", "--epic", eid)
    run(board, "limit", "target", agent="target")
    run(board, "msg", "hello seat", "--to", "target", agent="sender")
    srv = _Server(board, _free_port())
    try:
        d = srv.get()
        assert "epics" in d and any(e["id"] == eid for e in d["epics"])
        assert "attention" in d
        tgt = next(a for a in d["agents"] if a["name"] == "target")
        assert tgt.get("limit")
        msg = next(m for m in d["messages"] if "hello seat" in m.get("text", ""))
        assert msg.get("delivery", {}).get("status") == "direct"
        assert msg["delivery"]["acks"][0]["agent"] == "target"
        assert msg["delivery"]["acks"][0]["acked"] is False
        run(board, "inbox", agent="target")
        d2 = srv.get()
        msg2 = next(m for m in d2["messages"] if "hello seat" in m.get("text", ""))
        assert msg2["delivery"]["acks"][0]["acked"] is True
    finally:
        srv.stop()


def test_composer_task_kind_round_trips(board):
    run(board, "join", "boss", agent="boss")
    run(board, "join", "worker", agent="worker")
    srv = _Server(board, _free_port(), operator="boss")
    try:
        status, out = srv.post("/msg", {
            "from": "boss", "text": "claim T-001", "to": "worker", "re": "T-001", "kind": "task",
        })
        assert status == 200 and out["ok"]
        d = srv.get()
        msg = next(m for m in d["messages"] if m.get("kind") == "task")
        assert msg["text"] == "claim T-001"
    finally:
        srv.stop()
