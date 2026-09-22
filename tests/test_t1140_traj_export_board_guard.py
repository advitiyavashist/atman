"""T-1140: trajectories export --out must not land inside the board.

Reproduction (steer-split during T-1118):
  atm trajectories export --out <board>/agents/<seat>.json
silently replaced the seat record with JSONL (exit 0, no warning) on any
board. T-1118 only freezes the split shared board; this guards every board.

Both delivery paths must refuse: root tickets.py and packaged cli.py.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
ROOT_TOOL = ROOT / "tickets.py"
PKG_TOOL = ROOT / "src" / "ticket_board" / "cli.py"


def run_tool(tool, board_path, *args, agent="", cwd=None):
    e = dict(os.environ, TICKETS_DIR=str(board_path), TICKET_AGENT=agent or "",
             HOME=str(board_path.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    where = cwd or (board_path.parent if board_path.parent.is_dir() else Path("/"))
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                          text=True, env=e, cwd=where)


@pytest.fixture
def joined(board):
    """Board with one joined agent so agents/<seat>.json exists to protect."""
    repo = board.parent
    r = run_tool(ROOT_TOOL, board, "join", "alice", "--roles", "backend",
                 "--tool", "claude", "--model", "opus", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    run_tool(ROOT_TOOL, board, "create", "Ship it", "--role", "backend",
             agent="alice", cwd=repo)
    run_tool(ROOT_TOOL, board, "next", "--role", "backend",
             agent="alice", cwd=repo)
    return board


@pytest.mark.parametrize("tool", [ROOT_TOOL, PKG_TOOL], ids=["tickets.py", "cli.py"])
def test_export_refuses_out_inside_board_agents_record(tool, joined, tmp_path):
    b = joined
    repo = b.parent
    seat = b / "agents" / "alice.json"
    assert seat.is_file()
    before = seat.read_text()
    before_obj = json.loads(before)
    assert before_obj.get("name") == "alice" or "alice" in before

    r = run_tool(tool, b, "trajectories", "export", "--out", str(seat),
                 agent="alice", cwd=repo)
    text = r.stdout + r.stderr
    assert r.returncode != 0, "must refuse export onto agents/<seat>.json:\n" + text
    assert "REFUSING" in text
    assert "outside the ticket board" in text
    assert seat.read_text() == before, "agent record must be unchanged"
    assert not (b / "agents" / "alice.json.tmp").exists()

    # Sibling board paths (not just agents/) are also refused.
    other = b / "trajectories-dump.jsonl"
    r2 = run_tool(tool, b, "trajectories", "export", "--out", str(other),
                  agent="alice", cwd=repo)
    assert r2.returncode != 0, r2.stdout + r2.stderr
    assert not other.exists()

    # Outside the board still works.
    safe = tmp_path / "ok.jsonl"
    r3 = run_tool(tool, b, "trajectories", "export", "--out", str(safe),
                  agent="alice", cwd=repo)
    assert r3.returncode == 0, r3.stdout + r3.stderr
    assert safe.is_file()
    lines = [json.loads(x) for x in safe.read_text().splitlines() if x.strip()]
    assert lines


def test_helper_agrees_across_entrypoints(tmp_path):
    """Package helper classifies paths; root tickets.py keeps a named mirror."""
    sys.path.insert(0, str(ROOT / "src"))
    from ticket_board import trajectories as traj

    board_dir = tmp_path / "board"
    board_dir.mkdir()
    (board_dir / "agents").mkdir()
    inside = str(board_dir / "agents" / "x.json")
    outside = str(tmp_path / "outside.jsonl")
    assert traj.export_path_inside_board(inside, str(board_dir)) is True
    assert traj.export_path_inside_board(outside, str(board_dir)) is False
    src = ROOT_TOOL.read_text(encoding="utf-8")
    assert "def _traj_export_path_inside_board" in src
    assert "_refuse_traj_export_inside_board" in src
