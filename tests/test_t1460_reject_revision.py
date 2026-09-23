"""T-1460: REJECT / revise / retarget leave the worker able to act.

Reject and explicit reopen --revision return the ticket to its author as
claimed (API decideReview parity). Assigning an owner on an IN REVIEW ticket
makes it claimable for that seat (claimed, or reserved when they already hold
another). Throwaway boards only. Both delivery copies.
"""
from __future__ import annotations

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


def run(tool, board, *args, agent="", env=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    # Drop supervisor-pinned seat so ambient cursor-onboard/TICKET_SEAT from
    # the worker shell cannot outrank the per-call TICKET_AGENT (whoami).
    for key in ("TICKET_SEAT", "TICKET_SESSION_ID", "TICKETS_STOP_HOOK"):
        e.pop(key, None)
    e["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + e.get("PYTHONPATH", "")
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(tool), *args], capture_output=True, text=True,
        env=e, cwd=str(board.parent))


def load_ticket(board, tid):
    return json.loads((board / (tid + ".json")).read_text())


def save_ticket(board, t):
    (board / (t["id"] + ".json")).write_text(json.dumps(t, indent=2))


def repo_shas(board):
    repo = board.parent
    full = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    short = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, text=True).strip()
    return full, short


def put_in_review(board, tid="T-001", owner="alice", commit=None):
    t = load_ticket(board, tid)
    full, short = repo_shas(board)
    t["status"] = "review"
    t["owner"] = owner
    t["commit"] = commit or ("t1460@%s" % short)
    t["branch"] = "t1460"
    t["review_at"] = "2026-09-23T00:00:00Z"
    t.setdefault("notes", []).append({
        "by": owner, "at": "2026-09-23T00:00:00Z",
        "text": "REVIEW: %s -- paths: x" % t["commit"],
    })
    save_ticket(board, t)
    return t, full, short


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_reject_returns_author_as_claimed_and_shows_in_mine(tool, board):
    _, full, short = put_in_review(board)
    r = run(tool, board, "reject", "T-001", "--sha", short,
            "--reason", "tests missing", agent="reviewer")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "claimed for revision" in r.stdout
    t = load_ticket(board, "T-001")
    assert t["status"] == "claimed"
    assert t["owner"] == "alice"
    assert t["review_events"][0]["kind"] == "reject"
    mine = run(tool, board, "mine", agent="alice")
    assert mine.returncode == 0
    assert "T-001" in mine.stdout
    empty = run(tool, board, "mine", agent="reviewer")
    assert "no claimed tickets" in empty.stdout


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_reopen_revision_returns_author_claimed_without_pool_release(tool, board):
    put_in_review(board)
    run(tool, board, "join", "bob", "--roles", "docs", agent="bob")
    r = run(tool, board, "reopen", "T-001", "--revision",
            "--notes", "rebase onto main and resubmit", agent="planner")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "claimed for revision" in r.stdout
    t = load_ticket(board, "T-001")
    assert t["status"] == "claimed"
    assert t["owner"] == "alice"
    # Pool release must not happen: bob cannot next-claim alice's revision.
    stuck = run(tool, board, "next", agent="bob")
    assert stuck.returncode != 0
    mine = run(tool, board, "mine", agent="alice")
    assert "T-001" in mine.stdout


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_plain_reopen_still_releases_to_open(tool, board):
    """T-394 unchanged: noted reopen without --revision clears owner → open."""
    put_in_review(board)
    r = run(tool, board, "reopen", "T-001", "--notes", "FIX-FIRST: land the guard",
            agent="reviewer")
    assert r.returncode == 0, r.stderr
    t = load_ticket(board, "T-001")
    assert t["status"] == "open"
    assert t["owner"] == ""


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_assign_on_review_claims_free_reviewer(tool, board):
    put_in_review(board, owner="alice")
    run(tool, board, "join", "reviewer", "--roles", "docs", agent="reviewer")
    r = run(tool, board, "assign", "T-001", "--owner", "reviewer",
            agent="planner")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "returned from review" in r.stdout or "claimed for" in r.stdout
    t = load_ticket(board, "T-001")
    assert t["status"] == "claimed"
    assert t["owner"] == "reviewer"
    mine = run(tool, board, "mine", agent="reviewer")
    assert "T-001" in mine.stdout


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_assign_on_review_reserves_when_reviewer_already_holds(tool, board):
    put_in_review(board, owner="alice")
    assert run(tool, board, "join", "reviewer", "--roles", "docs",
               agent="reviewer").returncode == 0
    created = run(tool, board, "create", "Other hold", "--role", "docs",
                  agent="planner")
    assert created.returncode == 0, created.stderr
    other = "T-002"
    claim = run(tool, board, "claim", other, agent="reviewer")
    assert claim.returncode == 0, claim.stderr + claim.stdout
    r = run(tool, board, "assign", "T-001", "--owner", "reviewer",
            agent="planner")
    assert r.returncode == 0, r.stderr + r.stdout
    t = load_ticket(board, "T-001")
    assert t["status"] == "open"
    assert t["owner"] == ""
    assert t.get("reserved_for") == "reviewer"
    held = load_ticket(board, other)
    assert held["status"] == "claimed" and held["owner"] == "reviewer"
