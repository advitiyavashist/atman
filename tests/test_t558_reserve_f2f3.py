"""T-558: F2 next refusal names reservation vs role; F3 reserve validates.

F2: bob with the ticket's own role is refused a reserved ticket as
'reserved for other agents', never 'ready for other roles'.
F3: unknown --for warns and proceeds; claimed/done refuse.
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
    e["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + e.get("PYTHONPATH", "")
    where = cwd or board.parent
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                          text=True, env=e, cwd=str(where))


def show(tool, board, tid, agent="alice"):
    r = run(tool, board, "show", tid, "--json", agent=agent)
    assert r.returncode == 0, r.stderr + r.stdout
    return json.loads(r.stdout)


def setup_same_role_reserved(tool, board):
    # T-001 is docs (wakeup fixture). Both seats share that role so a
    # reservation cannot be explained as a roles.json miss.
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


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_next_refusal_names_reservation_not_role(tool, board):
    setup_same_role_reserved(tool, board)
    stuck = run(tool, board, "next", agent="bob")
    assert stuck.returncode != 0, stuck.stdout
    out = stuck.stdout + stuck.stderr
    assert "reserved for other agents" in out
    assert "T-001" in out
    assert "ready for other roles" not in out
    assert show(tool, board, "T-001")["status"] == "open"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_next_still_names_other_roles_when_role_misses(tool, board):
    setup_same_role_reserved(tool, board)
    assert run(tool, board, "join", "carol", "--roles", "backend", agent="carol").returncode == 0
    stuck = run(tool, board, "next", agent="carol")
    assert stuck.returncode != 0, stuck.stdout
    out = stuck.stdout + stuck.stderr
    assert "ready for other roles" in out
    assert "T-001" in out
    assert "reserved for other agents" not in out


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_reserve_unknown_name_warns_and_proceeds(tool, board):
    setup_same_role_reserved(tool, board)
    r = run(tool, board, "reserve", "T-001", "--for", "no-such-agent-xyz", agent="planner")
    assert r.returncode == 0, r.stderr + r.stdout
    out = r.stdout + r.stderr
    assert "warning" in out
    assert "no-such-agent-xyz" in out
    t = show(tool, board, "T-001")
    assert t.get("reserved_for") == "no-such-agent-xyz"
    assert t["status"] == "open"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_reserve_refuses_in_progress(tool, board):
    setup_same_role_reserved(tool, board)
    claimed = run(tool, board, "next", agent="alice")
    assert claimed.returncode == 0, claimed.stderr + claimed.stdout
    assert show(tool, board, "T-001")["status"] == "claimed"
    r = run(tool, board, "reserve", "T-001", "--for", "bob", agent="planner")
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "claimed" in out
    assert "open" in out
    t = show(tool, board, "T-001")
    assert t.get("reserved_for") == "alice"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_reserve_refuses_done(tool, board):
    setup_same_role_reserved(tool, board)
    claimed = run(tool, board, "next", agent="alice")
    assert claimed.returncode == 0, claimed.stderr + claimed.stdout
    done = run(tool, board, "done", "T-001", "--notes", "closed for f3", "--force",
                agent="planner")
    assert done.returncode == 0, done.stderr + done.stdout
    assert show(tool, board, "T-001")["status"] == "done"
    r = run(tool, board, "reserve", "T-001", "--for", "bob", agent="planner")
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "done" in out
    t = show(tool, board, "T-001")
    assert t.get("reserved_for") == "alice"
