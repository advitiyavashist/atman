"""T-345: team console Objective | Team | Work | Intervene on T-276/T-323."""
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
    "goals", "objective", "util", "in_flight", "review", "open", "agents",
    "health", "messages", "onboarding", "next_step", "empty_board",
)


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    end = text.index('"""', start)
    return text[start:end]


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

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def test_ui_html_is_team_console_four_surfaces():
    ui = _ui_html()
    for marker in (
        'data-tab-btn="objective"',
        'data-tab-btn="team"',
        'data-tab-btn="work"',
        'data-tab-btn="intervene"',
        ">Objective<",
        ">Team<",
        ">Work<",
        ">Intervene<",
        'data-team-btn="roster"',
        'data-team-btn="pitch"',
        "id=\"pane-objective\"",
        "id=\"pane-team\"",
        "id=\"pane-work\"",
        "id=\"pane-intervene\"",
        "id=\"acts\"",
        "id=\"objHonesty\"",
        "id=\"missionOne\"",
        "id=\"col-blocked\"",
        "id=\"band-keep\"",
        "id=\"mentionBar\"",
        "does not remote-control",
        "Not reported by harness",
        "No agents checked in",
        "No objective set",
        "renderIntervene",
        "setInterval(load,5000)",
        "fetch('/msg'",
        "ATMAN",
        "emptyBoard",
        "nextStep",
        "obSteps",
        "waiting on a fix or dependency",
        "Tickets",
        "Utilization",
        "Lane",
    ):
        assert marker in ui, "missing UI marker: %s" % marker
    assert 'data-tab-btn="board"' not in ui
    assert 'data-tab-btn="messages"' not in ui
    assert "DAG" not in ui
    assert "graph editor" not in ui.lower()


def test_board_snapshot_includes_objective_and_quota(board):
    run(board, "master", "take", agent="boss")
    run(board, "master", "init", agent="boss")
    run(board, "join", "worker", "--roles", "backend")
    r = run(board, "ui", "--json")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    for k in SNAPSHOT_KEYS:
        assert k in d, k
    assert d["objective"] is None
    worker = next(a for a in d["agents"] if a["name"] == "worker")
    assert "quota" in worker and "limit" in worker
    assert worker["quota"] is None
    assert worker["limit"] is False
    assert "roles" in worker

    run(board, "objective", "Ship the team console", agent="boss")
    run(board, "limit", "worker", "--until", "tonight", "--note", "quota", agent="worker")
    d2 = json.loads(run(board, "ui", "--json").stdout)
    assert d2["objective"]["text"] == "Ship the team console"
    assert d2["objective"]["done"] is False
    assert d2["objective"]["set_by"] == "boss"
    assert d2["goals"].startswith("OBJECTIVE")
    limited = next(a for a in d2["agents"] if a["name"] == "worker")
    assert limited["limit"] is True
    assert limited["quota"] and "limited" in limited["quota"]
    assert "tonight" in limited["quota"]


def test_live_html_serves_team_tabs(board):
    run(board, "join", "cursor", agent="cursor")
    srv = _Server(board, 18771)
    try:
        page = srv.get("/", raw=True).decode()
        d = srv.get()
    finally:
        srv.stop()
    assert 'data-tab-btn="objective"' in page
    assert 'data-tab-btn="work"' in page
    assert "does not remote-control" in page
    assert d["objective"] is None
    assert any(a["name"] == "cursor" for a in d["agents"])
