"""T-394: silent reopen / status todo of IN REVIEW work must fail.

cmd_next/try_claim only claim status=open. The live failure mode was
cmd_reopen (and `tickets status todo`, which calls it) setting status=open
with no reason, leaving review_at + REVIEW notes so the next owner treats
it as a next-reissue. try_claim's open-only gate is unchanged.

Both delivery copies are driven: tickets.py and src/ticket_board/cli.py.
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
    where = cwd or board.parent
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                          text=True, env=e, cwd=str(where))


def show(tool, board, tid, agent="alice"):
    r = run(tool, board, "show", tid, "--json", agent=agent)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def into_review(tool, board, tid="T-001"):
    run(tool, board, "join", "alice", "--roles", "docs", agent="alice")
    r = run(tool, board, "next", agent="alice")
    assert r.returncode == 0, r.stderr
    r = run(tool, board, "review", tid, "--notes", "paths tests", "--force",
            agent="alice")
    assert r.returncode == 0, r.stderr + r.stdout
    t = show(tool, board, tid)
    assert t["status"] == "review"
    assert t.get("review_at")
    return t


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_silent_reopen_of_review_fails_and_stays_review(tool, board):
    into_review(tool, board)
    before = show(tool, board, "T-001")
    r = run(tool, board, "reopen", "T-001", agent="alice")
    assert r.returncode != 0, r.stdout
    assert "needs --notes" in (r.stderr + r.stdout)
    after = show(tool, board, "T-001")
    assert after["status"] == "review"
    assert after.get("review_at") == before.get("review_at")
    assert after["owner"] == before["owner"]


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_silent_status_todo_of_review_fails(tool, board):
    into_review(tool, board)
    r = run(tool, board, "status", "T-001", "todo", agent="alice")
    assert r.returncode != 0, r.stdout
    assert "needs --notes" in (r.stderr + r.stdout)
    assert show(tool, board, "T-001")["status"] == "review"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_reopen_review_with_notes_opens_and_last_note_is_reason(tool, board):
    into_review(tool, board)
    reason = "FIX-FIRST: detector false positive"
    r = run(tool, board, "reopen", "T-001", "--notes", reason, "--by", "reviewer",
            agent="reviewer")
    assert r.returncode == 0, r.stderr
    t = show(tool, board, "T-001", agent="reviewer")
    assert t["status"] == "open"
    assert t["owner"] == ""
    assert t.get("review_at"), "review_at is history; do not clear it"
    assert t["notes"][-1]["text"] == reason
    assert t["notes"][-1]["by"] == "reviewer"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_next_claims_after_noted_reopen_never_claims_review(tool, board):
    into_review(tool, board)
    run(tool, board, "join", "bob", "--roles", "docs", agent="bob")
    stuck = run(tool, board, "next", agent="bob")
    assert stuck.returncode != 0, stuck.stdout
    assert show(tool, board, "T-001")["status"] == "review"

    r = run(tool, board, "reopen", "T-001", "--notes", "FIX-FIRST: land the guard",
            agent="reviewer")
    assert r.returncode == 0, r.stderr
    claimed = run(tool, board, "next", agent="bob")
    assert claimed.returncode == 0, claimed.stderr
    t = show(tool, board, "T-001", agent="bob")
    assert t["status"] == "claimed"
    assert t["owner"] == "bob"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_claimed_without_review_at_still_reopens_silently(tool, board):
    """T-246: never-reviewed claimed work stays reopenable without --notes."""
    run(tool, board, "join", "alice", "--roles", "docs", agent="alice")
    run(tool, board, "claim", "T-001", "--owner", "alice", agent="alice")
    r = run(tool, board, "reopen", "T-001", agent="alice")
    assert r.returncode == 0, r.stderr
    t = show(tool, board, "T-001")
    assert t["status"] == "open"
    assert t["owner"] == ""


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_silent_reopen_refused_when_review_at_lingers_after_reclaim(tool, board):
    into_review(tool, board)
    run(tool, board, "reopen", "T-001", "--notes", "FIX-FIRST: once", agent="reviewer")
    run(tool, board, "join", "bob", "--roles", "docs", agent="bob")
    run(tool, board, "next", agent="bob")
    r = run(tool, board, "reopen", "T-001", agent="bob")
    assert r.returncode != 0, r.stdout
    assert show(tool, board, "T-001")["status"] == "claimed"
    assert show(tool, board, "T-001")["owner"] == "bob"
