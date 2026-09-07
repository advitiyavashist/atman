"""T-344: turns UI panel -- board_snapshot.turns + UI_HTML markers."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ticket_board.turns import ROW_KEYS

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "turns_trajectories.jsonl"


def run(board, *args, agent="", env=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    if env:
        e.update(env)
    where = board.parent if board.parent.is_dir() else Path("/")
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, env=e, cwd=where)


@pytest.fixture
def board(tmp_path):
    b = tmp_path / "proj" / ".tickets"
    b.mkdir(parents=True)
    return b


def test_board_snapshot_turns_is_frozen_json(board):
    shutil.copy(FIXTURE, board / "trajectories.jsonl")
    run(board, "master", "take", agent="boss")
    run(board, "master", "init", agent="boss")
    r = run(board, "ui", "--json")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert "turns" in d
    turns = d["turns"]
    assert turns["v"] == 1
    assert "tickets" in turns and "aggregates" in turns
    agg = turns["aggregates"]
    for k in ("mean", "median", "n", "n_unmeasured", "by_agent", "by_model", "by_role", "by_priority"):
        assert k in agg
    by = {row["ticket"]: row for row in turns["tickets"]}
    assert by["T-010"]["turns"] == 2
    assert by["T-012"]["turns"] is None
    for row in turns["tickets"]:
        assert tuple(row.keys()) == ROW_KEYS
    measured = [row for row in turns["tickets"] if row["turns"] is not None]
    worst = sorted(measured, key=lambda r: (-r["turns"], r["ticket"]))[:10]
    assert worst[0]["ticket"] == "T-011" or worst[0]["turns"] >= 2
    agents = {rec["agent"]: rec for rec in agg["by_agent"]}
    assert agents["alice"]["median"] == 2.0


def test_board_snapshot_turns_empty_without_trajectories(board):
    run(board, "master", "take", agent="boss")
    run(board, "master", "init", agent="boss")
    r = run(board, "ui", "--json")
    assert r.returncode == 0, r.stderr
    turns = json.loads(r.stdout)["turns"]
    assert turns["v"] == 1
    assert turns["tickets"] == []
    assert turns["aggregates"]["n"] == 0


def test_ui_html_contains_turns_panel_markers():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    end = text.index('"""', start)
    html = text[start:end]
    for marker in (
        "Turns to done",
        "turnsWorst",
        "turnsAgents",
        "turnsSummary",
        "renderTurns",
        "turns-grid",
        "Worst tickets",
        "Per-agent median",
        "no run_end",
    ):
        assert marker in html, "missing UI marker: %s" % marker


def test_worst_ten_sort_matches_cli_json(board):
    shutil.copy(FIXTURE, board / "trajectories.jsonl")
    run(board, "master", "take", agent="boss")
    run(board, "master", "init", agent="boss")
    snap = json.loads(run(board, "ui", "--json").stdout)["turns"]
    cli = json.loads(run(board, "turns", "--json").stdout)
    assert snap == cli
    measured = [r for r in cli["tickets"] if r["turns"] is not None]
    worst = sorted(measured, key=lambda r: (-r["turns"], r["ticket"]))[:10]
    assert len(worst) <= 10
    if len(measured) >= 2:
        assert worst[0]["turns"] >= worst[-1]["turns"]
