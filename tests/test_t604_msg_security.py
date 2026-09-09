"""T-604 security bounce: POST /msg is JSON + same-origin + registered from."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from test_wakeup import board, run  # noqa: F401

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
    def __init__(self, board, port):
        self.port = port
        env = dict(os.environ, TICKETS_DIR=str(board))
        self.proc = subprocess.Popen(
            [sys.executable, str(TOOL), "ui", "--port", str(port), "--host", "127.0.0.1"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if not _wait_up(port):
            self.stop()
            raise RuntimeError("tickets ui never came up on %d" % port)

    def post(self, path, payload, headers=None, content_type="application/json", raw_body=None):
        data = raw_body if raw_body is not None else json.dumps(payload).encode()
        head = {"Content-Type": content_type}
        if headers:
            head.update(headers)
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path),
            data=data, method="POST", headers=head,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def get(self, path="/board.json"):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path), timeout=5) as r:
            return json.loads(r.read())

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def _live(board):
    p = board / "messages.jsonl"
    return p.read_text() if p.exists() else ""


def test_rejects_text_plain_cross_origin(board):
    run(board, "join", "boss", agent="boss")
    run(board, "join", "worker", agent="worker")
    srv = _Server(board, _free_port())
    try:
        body = json.dumps({
            "from": "boss", "text": "plain-cross-origin-task", "to": "worker",
            "kind": "task",
        }).encode()
        status, out = srv.post(
            "/msg", None, content_type="text/plain", raw_body=body,
            headers={"Origin": "http://evil.example"},
        )
        assert status == 400 and not out["ok"]
        assert "json" in (out.get("error") or "").lower()
        assert "plain-cross-origin-task" not in _live(board)
    finally:
        srv.stop()


def test_rejects_wrong_origin(board):
    run(board, "join", "boss", agent="boss")
    run(board, "join", "worker", agent="worker")
    srv = _Server(board, _free_port())
    try:
        status, out = srv.post(
            "/msg",
            {"from": "boss", "text": "wrong-origin-task", "to": "worker", "kind": "task"},
            headers={"Origin": "http://evil.example"},
        )
        assert status == 400 and not out["ok"]
        assert "origin" in (out.get("error") or "").lower()
        assert "wrong-origin-task" not in _live(board)
    finally:
        srv.stop()


def test_rejects_unregistered_from(board):
    run(board, "join", "worker", agent="worker")
    srv = _Server(board, _free_port())
    try:
        status, out = srv.post(
            "/msg",
            {"from": "spoofed", "text": "unregistered-from-task", "to": "worker", "kind": "task"},
        )
        assert status == 400 and not out["ok"]
        err = (out.get("error") or "").lower()
        assert "registered" in err or "from" in err
        assert "unregistered-from-task" not in _live(board)
    finally:
        srv.stop()


def test_same_origin_registered_task_succeeds(board):
    run(board, "join", "boss", agent="boss")
    run(board, "join", "worker", agent="worker")
    srv = _Server(board, _free_port())
    try:
        origin = "http://127.0.0.1:%d" % srv.port
        status, out = srv.post(
            "/msg",
            {"from": "boss", "text": "claim T-001", "to": "worker", "re": "T-001", "kind": "task"},
            headers={"Origin": origin},
        )
        assert status == 200 and out["ok"], out
        d = srv.get()
        msg = next(m for m in d["messages"] if m.get("kind") == "task")
        assert msg["text"] == "claim T-001"
        assert msg["from"] == "boss"
        assert "claim T-001" in _live(board)
    finally:
        srv.stop()
