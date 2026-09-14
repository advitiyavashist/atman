"""T-979: assign/claim refuse a second active hold on disposable boards.

T-972 reproduced `atm assign --owner` giving a worker a second in-progress
ticket. Mutation: delete the hold check in cmd_assign / try_claim_one_active
and the busy-worker cases red.
"""
import json
import os
import subprocess
import sys
import threading
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
    for var in ("TICKET_SEAT", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID",
                "CURSOR_SESSION_ID", "TERM_SESSION_ID"):
        e.pop(var, None)
    if args and args[0] == "join" and len(args) > 1 and args[1]:
        actor = args[1]
    else:
        actor = agent or "__anonymous__"
    e["TICKET_SESSION_ID"] = "test-session-" + actor
    where = cwd or board.parent
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                          text=True, env=e, cwd=str(where))


def show(tool, board, tid, agent="alice"):
    r = run(tool, board, "show", tid, "--json", agent=agent)
    assert r.returncode == 0, r.stderr + r.stdout
    return json.loads(r.stdout)


def snap(board, tid):
    return (board / (tid + ".json")).read_text()


def setup_two_ready(tool, board):
    assert run(tool, board, "join", "alice", "--roles", "docs", agent="alice").returncode == 0
    assert run(tool, board, "join", "bob", "--roles", "docs", agent="bob").returncode == 0
    assert run(tool, board, "join", "planner", "--roles", "backend", agent="planner").returncode == 0
    created = run(tool, board, "create", "Second ready ticket", "--role", "docs", agent="planner")
    assert created.returncode == 0, created.stderr + created.stdout
    assert "T-002" in created.stdout
    return "T-001", "T-002"


def claimed_by(tool, board, owner):
    return [tid for tid in ("T-001", "T-002", "T-003")
            if (board / (tid + ".json")).exists()
            and show(tool, board, tid)["status"] == "claimed"
            and show(tool, board, tid).get("owner") == owner]


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_assign_to_busy_worker_reserves_instead_of_second_claim(tool, board):
    a, b = setup_two_ready(tool, board)
    claimed = run(tool, board, "claim", a, agent="alice")
    assert claimed.returncode == 0, claimed.stderr + claimed.stdout
    before_a = snap(board, a)
    assigned = run(tool, board, "assign", b, "--owner", "alice", agent="planner")
    assert assigned.returncode == 0, assigned.stderr + assigned.stdout
    assert "reserved for alice" in assigned.stdout
    assert "claimed for alice" not in assigned.stdout
    assert snap(board, a) == before_a
    tb = show(tool, board, b)
    assert tb["status"] == "open"
    assert tb.get("owner") in ("", None)
    assert tb.get("reserved_for") == "alice"
    assert claimed_by(tool, board, "alice") == [a]


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_claim_second_active_refuses_and_leaves_records_unchanged(tool, board):
    a, b = setup_two_ready(tool, board)
    assert run(tool, board, "claim", a, agent="alice").returncode == 0
    before_a, before_b = snap(board, a), snap(board, b)
    refused = run(tool, board, "claim", b, agent="alice")
    assert refused.returncode != 0
    assert "already hold %s" % a in (refused.stderr + refused.stdout)
    assert snap(board, a) == before_a
    assert snap(board, b) == before_b
    assert show(tool, board, b)["status"] == "open"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_after_review_queued_ticket_can_be_assigned_active(tool, board):
    a, b = setup_two_ready(tool, board)
    repo = board.parent
    assert run(tool, board, "claim", a, agent="alice", cwd=repo).returncode == 0
    reviewed = run(tool, board, "review", a, "--notes", "paths tests",
                   "--force", agent="alice", cwd=repo)
    assert reviewed.returncode == 0, reviewed.stderr + reviewed.stdout
    assigned = run(tool, board, "assign", b, "--owner", "alice", agent="planner")
    assert assigned.returncode == 0, assigned.stderr + assigned.stdout
    assert "claimed for alice" in assigned.stdout
    tb = show(tool, board, b)
    assert tb["status"] == "claimed"
    assert tb["owner"] == "alice"
    assert show(tool, board, a)["status"] == "review"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_after_reopen_queued_ticket_can_be_claimed(tool, board):
    a, b = setup_two_ready(tool, board)
    assert run(tool, board, "claim", a, agent="alice").returncode == 0
    reopened = run(tool, board, "reopen", a, "--notes", "hand back", agent="alice")
    assert reopened.returncode == 0, reopened.stderr + reopened.stdout
    assert show(tool, board, a)["status"] == "open"
    claimed = run(tool, board, "claim", b, agent="alice")
    assert claimed.returncode == 0, claimed.stderr + claimed.stdout
    assert show(tool, board, b)["status"] == "claimed"
    assert show(tool, board, b)["owner"] == "alice"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_ownership_transfer_to_free_worker_still_works(tool, board):
    a, _b = setup_two_ready(tool, board)
    assert run(tool, board, "claim", a, agent="alice").returncode == 0
    moved = run(tool, board, "assign", a, "--owner", "bob", agent="planner")
    assert moved.returncode == 0, moved.stderr + moved.stdout
    ta = show(tool, board, a)
    assert ta["status"] == "claimed"
    assert ta["owner"] == "bob"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_transfer_claimed_ticket_to_busy_worker_refuses_unchanged(tool, board):
    a, b = setup_two_ready(tool, board)
    assert run(tool, board, "claim", a, agent="alice").returncode == 0
    assert run(tool, board, "claim", b, agent="bob").returncode == 0
    before_a, before_b = snap(board, a), snap(board, b)
    refused = run(tool, board, "assign", b, "--owner", "alice", agent="planner")
    assert refused.returncode != 0
    assert "already holds %s" % a in (refused.stderr + refused.stdout)
    assert snap(board, a) == before_a
    assert snap(board, b) == before_b


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_concurrent_assign_and_claim_yield_at_most_one_active(tool, board):
    a, b = setup_two_ready(tool, board)
    results = []

    def go(args, agent):
        results.append(run(tool, board, *args, agent=agent))

    t1 = threading.Thread(target=go, args=(("assign", a, "--owner", "alice"), "planner"))
    t2 = threading.Thread(target=go, args=(("claim", b), "alice"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    held = claimed_by(tool, board, "alice")
    assert len(held) <= 1, held
    statuses = {tid: show(tool, board, tid)["status"] for tid in (a, b)}
    assert sum(1 for s in statuses.values() if s == "claimed") <= 1


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_concurrent_double_assign_yields_at_most_one_active(tool, board):
    a, b = setup_two_ready(tool, board)
    results = []

    def go(tid):
        results.append(run(tool, board, "assign", tid, "--owner", "alice", agent="planner"))

    t1 = threading.Thread(target=go, args=(a,))
    t2 = threading.Thread(target=go, args=(b,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert all(r.returncode == 0 for r in results), [r.stderr + r.stdout for r in results]
    held = claimed_by(tool, board, "alice")
    assert len(held) == 1, held
    other = b if held[0] == a else a
    to = show(tool, board, other)
    assert to["status"] == "open"
    assert to.get("reserved_for") == "alice"
