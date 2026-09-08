"""T-276 command-board UI + T-221 composer: /msg posts through post_message(),
same messages.jsonl, no second store. Snapshot keys stay stable."""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from test_wakeup import board, run  # noqa: F401
from ui_server_harness import UiServer, _free_port, make_ui_server_fixture, port_is_dead

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"
ui_server = make_ui_server_fixture("t276-probe")

SNAPSHOT_KEYS = (
    "project", "generated", "master", "cos", "counts", "sprint", "burn",
    "goals", "util", "in_flight", "review", "open", "agents", "health", "messages",
)


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
    import os
    import signal

    foreign_repo = board.parent.parent / "foreign-repo"
    foreign_repo.mkdir(exist_ok=True)
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
        monkeypatch.setattr("ui_server_harness._free_port", lambda: port)
        with pytest.raises(RuntimeError, match="foreign tickets ui"):
            UiServer(board, probe_prefix="t276-probe")
    finally:
        try:
            os.killpg(os.getpgid(stray.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            stray.terminate()
        stray.wait(timeout=5)


def test_fixture_stops_server_after_assertion_failure(board, ui_server):
    """Covers the no-teardown gap: ui_server fixture finalizer must stop ui on body failure."""
    from ui_server_harness import FIXTURE_TEETH_PORT_FILE

    FIXTURE_TEETH_PORT_FILE.write_text(str(ui_server.port))
    with pytest.raises(AssertionError, match="simulated test failure"):
        raise AssertionError("simulated test failure")


def test_fixture_stops_server_after_assertion_failure_teeth(board):
    """Runs after the failing test above: fixture finalizer must have stopped the port."""
    from ui_server_harness import FIXTURE_TEETH_PORT_FILE

    assert FIXTURE_TEETH_PORT_FILE.exists(), "fixture teeth port file missing"
    port = int(FIXTURE_TEETH_PORT_FILE.read_text().strip())
    FIXTURE_TEETH_PORT_FILE.unlink(missing_ok=True)
    assert port_is_dead(port), "port %d still serving after ui_server fixture teardown" % port
