"""T-557: tickets route must not write suggested= onto a reserved ticket.

T-553 F1: _needs_route's deps-done branch ignored reserved_for, so route
stamped suggested=<other> the moment the dep closed. next still refused
that ticket. Isolated CLI: reserve T-001 for alice, route --only bob,
assert reserved_for stays alice and suggested is absent; bob's next
refuses; alice's next claims.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]


def run(tool, board, *args, agent="", cwd=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    # cli.py cmd_route imports ticket_board.scheduler; running the file
    # as a script needs src/ on PYTHONPATH (tickets.py has a T-550 fallback).
    e["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + e.get("PYTHONPATH", "")
    where = cwd or board.parent
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                          text=True, env=e, cwd=str(where))


def show(tool, board, tid, agent="alice"):
    r = run(tool, board, "show", tid, "--json", agent=agent)
    assert r.returncode == 0, r.stderr + r.stdout
    return json.loads(r.stdout)


def setup_reserved(tool, board):
    assert run(tool, board, "join", "alice", "--roles", "docs", agent="alice").returncode == 0
    assert run(tool, board, "join", "bob", "--roles", "docs", agent="bob").returncode == 0
    assert run(tool, board, "join", "planner", "--roles", "backend", agent="planner").returncode == 0
    r = run(tool, board, "master", "take", "--owner", "planner", agent="planner")
    assert r.returncode == 0, r.stderr + r.stdout
    r = run(tool, board, "reserve", "T-001", "--for", "alice", agent="planner")
    assert r.returncode == 0, r.stderr + r.stdout
    t = show(tool, board, "T-001")
    assert t["status"] == "open"
    assert t.get("reserved_for") == "alice"
    assert not t.get("suggested")


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_route_does_not_write_suggested_on_reserved_ticket(tool, board):
    setup_reserved(tool, board)
    routed = run(tool, board, "route", "--only", "bob", agent="planner")
    assert routed.returncode == 0, routed.stderr + routed.stdout
    t = show(tool, board, "T-001")
    assert t.get("reserved_for") == "alice"
    assert not t.get("suggested"), t
    stuck = run(tool, board, "next", agent="bob")
    assert stuck.returncode != 0, stuck.stdout
    claimed = run(tool, board, "next", agent="alice")
    assert claimed.returncode == 0, claimed.stderr + claimed.stdout
    t = show(tool, board, "T-001")
    assert t["status"] == "claimed"
    assert t["owner"] == "alice"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_route_still_suggests_after_reservation_drop(tool, board):
    setup_reserved(tool, board)
    dropped = run(tool, board, "reserve", "T-001", "--drop", agent="planner")
    assert dropped.returncode == 0, dropped.stderr + dropped.stdout
    routed = run(tool, board, "route", "--only", "bob", agent="planner")
    assert routed.returncode == 0, routed.stderr + routed.stdout
    t = show(tool, board, "T-001")
    assert not t.get("reserved_for")
    assert t.get("suggested") == "bob"
