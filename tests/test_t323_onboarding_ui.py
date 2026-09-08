"""T-323: onboarding UI -- board_snapshot fields + UI_HTML string markers."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def run(board, *args, agent="", env=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "", HOME=str(board.parent.parent / "home"))
    if env:
        e.update(env)
    where = board.parent if board.parent.is_dir() else Path("/")
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, env=e, cwd=where)


@pytest.fixture
def board(tmp_path):
    b = tmp_path / "proj" / ".tickets"
    b.mkdir(parents=True)
    return b


def test_board_snapshot_includes_onboarding_and_next_step(board):
    run(board, "master", "take", agent="boss")
    run(board, "master", "init", agent="boss")
    run(board, "join", "worker", "--roles", "backend")
    r = run(board, "ui", "--json")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    for k in ("onboarding", "next_step", "empty_board"):
        assert k in d
    ob = d["onboarding"]
    for step in ("initialized", "first_ticket", "first_agent", "first_review", "first_merge", "objective_set"):
        assert step in ob
        assert isinstance(ob[step], bool)
    assert ob["initialized"] is True
    assert ob["first_agent"] is True
    ns = d["next_step"]
    assert ns["kind"] in ("unblock", "merge", "spawn", "route", "start", "ok")
    assert ns.get("message") and ns.get("label")


def test_empty_board_snapshot_and_next_step_start(board):
    r = run(board, "ui", "--json")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["empty_board"] is True
    assert d["counts"]["total"] == 0
    assert d["next_step"]["kind"] == "start"
    assert "quickstart" in d["next_step"]["cmd"]


def test_ui_html_contains_onboarding_markers():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    end = text.index('"""', start)
    html = text[start:end]
    for marker in (
        "emptyBoard",
        "nextStep",
        "obSteps",
        "renderOnboarding",
        "renderNextStep",
        "renderEmptyBoard",
        "unreachableNextStep",
        "snapshotFails",
        "tickets quickstart",
        "Board ready",
        "Turns",
        "Utilization",
        "Lane",
        "waiting on a fix or dependency",
        "emptySteps",
        "Objective · Team · Work · Intervene",
        "Done(24h)",
    ):
        assert marker in html, "missing UI marker: %s" % marker


def test_next_step_spawn_when_no_agents(board):
    run(board, "master", "take", agent="boss")
    run(board, "master", "init", agent="boss")
    run(board, "create", "hello", agent="boss")
    r = run(board, "ui", "--json")
    d = json.loads(r.stdout)
    assert d["next_step"]["kind"] == "spawn"
    assert d["onboarding"]["first_ticket"] is True
    assert d["onboarding"]["first_agent"] is False


def test_snapshot_error_payload_has_next_step():
    """Server fallback when board_snapshot() throws must still guide the user."""
    text = TOOL.read_text(encoding="utf-8")
    assert '"kind": "unreachable"' in text
    assert '"error": "snapshot failed"' in text
    assert "next_step" in text.split("snapshot failed", 1)[1]
