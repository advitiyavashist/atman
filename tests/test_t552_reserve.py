"""T-552: tickets next honours reserved_for without claiming.

Fixture board: reserve T for A; next as B returns none; next as A returns T;
drop reservation then B gets it. Mutation: delete the skip in cmd_next
(_reservation_blocks filter) and the B case reds.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401
from ticket_board.cli import _reservation_blocks

ROOT = Path(__file__).resolve().parents[1]
TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]


def run(tool, board, *args, agent="", cwd=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    where = cwd or board.parent
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                          text=True, env=e, cwd=str(where))


def show(tool, board, tid, agent="alice"):
    r = run(tool, board, "show", tid, "--json", agent=agent)
    assert r.returncode == 0, r.stderr + r.stdout
    return json.loads(r.stdout)


def setup_lane(tool, board):
    assert run(tool, board, "join", "alice", "--roles", "docs", agent="alice").returncode == 0
    assert run(tool, board, "join", "bob", "--roles", "docs", agent="bob").returncode == 0
    assert run(tool, board, "join", "planner", "--roles", "backend", agent="planner").returncode == 0
    r = run(tool, board, "master", "take", "--owner", "planner", agent="planner")
    assert r.returncode == 0, r.stderr + r.stdout
    r = run(tool, board, "reserve", "T-001", "--for", "alice", agent="planner")
    assert r.returncode == 0, r.stderr + r.stdout
    t = show(tool, board, "T-001")
    assert t["status"] == "open"
    assert t.get("owner") in ("", None)
    assert t.get("reserved_for") == "alice"
    listed = run(tool, board, "list", agent="planner")
    assert listed.returncode == 0
    assert "reserved: alice" in listed.stdout


def test_reservation_blocks_helper():
    t = {"id": "T-001", "reserved_for": "alice"}
    assert _reservation_blocks(t, "bob") is True
    assert _reservation_blocks(t, "alice") is False
    assert _reservation_blocks(t, "bob", steal_id="T-001") is False
    assert _reservation_blocks({"id": "T-001"}, "bob") is False


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_next_skips_reservation_for_other_agent(tool, board):
    setup_lane(tool, board)
    stuck = run(tool, board, "next", agent="bob")
    assert stuck.returncode != 0, stuck.stdout
    assert show(tool, board, "T-001")["status"] == "open"
    claimed = run(tool, board, "next", agent="alice")
    assert claimed.returncode == 0, claimed.stderr + claimed.stdout
    t = show(tool, board, "T-001")
    assert t["status"] == "claimed"
    assert t["owner"] == "alice"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_drop_reservation_lets_other_agent_claim(tool, board):
    setup_lane(tool, board)
    dropped = run(tool, board, "reserve", "T-001", "--drop", agent="bob")
    assert dropped.returncode == 0, dropped.stderr + dropped.stdout
    t = show(tool, board, "T-001")
    assert not t.get("reserved_for")
    assert t["status"] == "open"
    claimed = run(tool, board, "next", agent="bob")
    assert claimed.returncode == 0, claimed.stderr + claimed.stdout
    t = show(tool, board, "T-001")
    assert t["status"] == "claimed"
    assert t["owner"] == "bob"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_steal_overrides_reservation(tool, board):
    setup_lane(tool, board)
    stolen = run(tool, board, "next", "--steal", "T-001", agent="bob")
    assert stolen.returncode == 0, stolen.stderr + stolen.stdout
    t = show(tool, board, "T-001")
    assert t["status"] == "claimed"
    assert t["owner"] == "bob"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_non_authority_cannot_set_reservation(tool, board):
    run(tool, board, "join", "bob", "--roles", "docs", agent="bob")
    r = run(tool, board, "reserve", "T-001", "--for", "bob", agent="bob")
    assert r.returncode != 0
    assert "master/planner/optimizer" in (r.stderr + r.stdout)
    assert "reserved_for" not in show(tool, board, "T-001", agent="bob")
