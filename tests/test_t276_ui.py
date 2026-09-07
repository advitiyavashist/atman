"""T-276 command-board UI + T-221 composer: /msg posts through post_message(),
same messages.jsonl, no second store. Snapshot keys stay stable."""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"

SNAPSHOT_KEYS = (
    "project", "generated", "master", "cos", "counts", "sprint", "burn",
    "goals", "util", "in_flight", "review", "open", "agents", "health", "messages",
)


def _wait_up(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/board.json" % port, timeout=1)
            return True
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.1)
    return False


class _Server:
    def __init__(self, board, port):
        self.port = port
        env = dict(os.environ, TICKETS_DIR=str(board))
        self.proc = subprocess.Popen(
            [sys.executable, str(TOOL), "ui", "--port", str(port), "--host", "127.0.0.1"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if not _wait_up(port):
            self.stop()
            raise RuntimeError("tickets ui never came up")

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
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def test_ui_html_is_command_board_not_spreadsheet(board):
    html = TOOL.read_text()
    start = html.index('UI_HTML = r"""')
    ui = html[start:html.index('"""', start + 14)]
    assert "--bg:#0c0e12" in ui
    assert 'data-tab-btn="objective"' in ui
    assert 'data-tab-btn="team"' in ui
    assert 'data-tab-btn="work"' in ui
    assert 'data-tab-btn="intervene"' in ui
    assert "Total Football." in ui
    assert 'id="band-keep"' in ui and 'id="band-attack"' in ui
    assert 'id="col-blocked"' in ui and 'id="col-ready"' in ui
    assert 'id="col-flight"' in ui and 'id="col-review"' in ui
    assert 'id="missionOne"' in ui
    assert 'id="mentionBar"' in ui and 'id="cSend"' in ui
    assert "fetch('/msg'" in ui
    assert "setInterval(load,5000)" in ui
    assert 'id="open"' not in ui  # old spreadsheet dump is gone


def test_composer_post_lands_in_the_same_board_and_reaches_mentioned_agent(board):
    run(board, "join", "cursor", agent="cursor")
    run(board, "join", "alice", agent="alice")
    srv = _Server(board, 18761)
    try:
        status, out = srv.post("/msg", {"from": "alice", "text": "@cursor check the composer", "to": ""})
        assert status == 200 and out["ok"], out
        assert "cursor" in out["posted"]
        page = srv.get("/", raw=True).decode()
        assert 'data-tab-btn="work"' in page
        assert 'id="col-blocked"' in page
    finally:
        srv.stop()

    live = (board / "messages.jsonl").read_text()
    assert "check the composer" in live
    out = run(board, "inbox", agent="cursor").stdout
    assert "check the composer" in out


def test_composer_rejects_missing_fields_without_touching_the_board(board):
    srv = _Server(board, 18762)
    try:
        status, out = srv.post("/msg", {"from": "", "text": "no sender"})
        assert status == 400 and not out["ok"]
        status, out = srv.post("/msg", {"from": "alice", "text": ""})
        assert status == 400 and not out["ok"]
    finally:
        srv.stop()
    assert not (board / "messages.jsonl").exists() or "no sender" not in (board / "messages.jsonl").read_text()


def test_board_json_keeps_snapshot_keys_and_feeds_mentions(board):
    run(board, "join", "cursor", agent="cursor")
    run(board, "msg", "@cursor look at this", agent="alice")
    srv = _Server(board, 18763)
    try:
        d = srv.get()
    finally:
        srv.stop()
    for k in SNAPSHOT_KEYS:
        assert k in d, k
    assert any(a["name"] == "cursor" for a in d["agents"])
    assert any(m["from"] == "alice" and "cursor" in (m.get("mentions") or []) for m in d["messages"])
