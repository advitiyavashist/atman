"""T-1082: `atm done` records the accepted sha, not a later tree HEAD.

Two-copy: tickets.py and src/ticket_board/cli.py.
Throwaway board. Coordinator close from trunk, or `done --artifact` at a
tree whose HEAD moved past the accept, must not hand that later sha to
the successor.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401
from test_t1031_accept_gate import (
    TOOLS,
    TOOL_IDS,
    load_ticket,
    put_in_review,
    repo_shas,
    run,
    setup_backend,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import work_view  # noqa: E402


def _git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    return subprocess.check_output(["git", "-C", str(repo), *args],
                                   text=True, env=env).strip()


def later_commit(board, msg="later trunk"):
    repo = board.parent
    (repo / "later.txt").write_text(msg + "\n")
    _git(repo, "add", "later.txt")
    _git(repo, "commit", "-q", "-m", msg)
    return repo_shas(board)


def test_done_pin_state_defaults_to_accepted_sha():
    accepted = "a" * 40
    later = "b" * 40
    t = {
        "id": "T-001",
        "status": "review",
        "review_head": accepted,
        "branch": "fix/t1082",
        "commit": "fix/t1082@" + accepted[:7],
        "review_events": [{
            "kind": "accept", "by": "rev", "at": "2026-09-18T00:00:00Z",
            "sha": accepted, "notes": "ok",
        }],
    }
    g = {"branch": "main", "sha": later[:7], "sha_full": later, "repo": "x"}
    pin, warn = work_view.done_pin_state(t, g)
    assert pin["sha_full"] == accepted
    assert pin["sha"].startswith(accepted[:7])
    assert pin["branch"] == "fix/t1082"
    assert warn and later[:12] in warn and accepted[:12] in warn
    assert "point --artifact at the accepted worktree" in warn
    assert "re-accept" in warn
    same, no_warn = work_view.done_pin_state(
        t, {"branch": "main", "sha": accepted[:7], "sha_full": accepted})
    assert same["sha_full"] == accepted
    assert no_warn is None
    honoured, honoured_warn = work_view.done_pin_state(t, g, honor_cwd=True)
    assert honoured["sha_full"] == accepted
    assert honoured["branch"] == "fix/t1082"
    assert honoured_warn and later[:12] in honoured_warn
    assert accepted[:12] in honoured_warn
    assert "point --artifact at the accepted worktree" in honoured_warn
    assert "re-accept" in honoured_warn
    bare, _ = work_view.done_pin_state({"id": "T-009"}, g)
    assert bare is g


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_done_without_artifact_records_accepted_sha_not_cwd(tool, board):
    setup_backend(tool, board)
    accepted_full, accepted_short = put_in_review(board)
    acc = run(tool, board, "accept", "T-002", "--sha", accepted_full,
              "--notes", "verified artifact", agent="reviewer")
    assert acc.returncode == 0, acc.stderr + acc.stdout

    later_full, later_short = later_commit(board)
    assert later_full != accepted_full
    assert later_short != accepted_short

    done = run(tool, board, "done", "T-002", "--notes", "handoff paths",
               agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    err = done.stderr + done.stdout
    assert "the accepted commit is" in err
    assert "point --artifact at the accepted worktree" in err
    assert later_short in err or later_full[:12] in err

    parent = load_ticket(board, "T-002")
    pin = parent.get("commit") or ""
    assert accepted_short in pin, pin
    assert later_short not in pin, pin
    notes = " ".join(n.get("text") or "" for n in parent.get("notes") or [])
    assert accepted_short in notes
    assert later_short not in notes
    assert "handoff paths" in notes

    child = load_ticket(board, "T-003")
    assert child["status"] == "open"
    shown = run(tool, board, "show", "T-003", agent="bob")
    assert shown.returncode == 0, shown.stderr + shown.stdout
    handoff = shown.stdout
    assert "Handoff from dependencies" in handoff
    assert accepted_short in handoff
    assert accepted_full in handoff
    assert later_short not in handoff
    assert "main@%s" % later_short not in handoff


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_done_without_accept_still_records_cwd(tool, board):
    setup_backend(tool, board)
    put_in_review(board)
    later_full, later_short = later_commit(board)
    done = run(tool, board, "done", "T-002", "--notes", "no accept yet",
               "--force", agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    parent = load_ticket(board, "T-002")
    pin = parent.get("commit") or ""
    assert later_short in pin, pin
    assert "the accepted commit is" not in (done.stderr or "")


def _later_commit_in(tree, msg="later artifact"):
    (Path(tree) / "later-artifact.txt").write_text(msg + "\n")
    _git(tree, "add", "later-artifact.txt")
    _git(tree, "commit", "-q", "-m", msg)
    full = _git(tree, "rev-parse", "HEAD")
    short = _git(tree, "rev-parse", "--short", "HEAD")
    return full, short


def test_done_artifact_moved_head_does_not_hand_unreviewed_sha(board):
    """README close: done --artifact at a tree whose HEAD moved past accept.

    cli.py has no --artifact; tickets.py is the atm CLI this documents.
    """
    tool = TOOLS[0]
    setup_backend(tool, board)
    accepted_full, accepted_short = put_in_review(board)
    acc = run(tool, board, "accept", "T-002", "--sha", accepted_full,
              "--notes", "verified artifact", agent="reviewer")
    assert acc.returncode == 0, acc.stderr + acc.stdout

    repo = board.parent
    wt = repo / ".worktrees" / "worker"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", "fix/t1082-later", str(wt), "HEAD")
    later_full, later_short = _later_commit_in(wt)
    assert later_full != accepted_full
    assert later_short != accepted_short

    done = run(tool, board, "done", "T-002", "--artifact", str(wt),
               "--notes", "handoff paths", agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    err = done.stderr + done.stdout
    assert "that tree is at" in err
    assert "the accepted commit is" in err
    assert "point --artifact at the accepted worktree" in err
    assert "re-accept" in err
    assert later_short in err or later_full[:12] in err

    parent = load_ticket(board, "T-002")
    pin = parent.get("commit") or ""
    assert accepted_short in pin, pin
    assert later_short not in pin, pin
    notes = " ".join(n.get("text") or "" for n in parent.get("notes") or [])
    assert accepted_short in notes
    assert later_short not in notes

    child = load_ticket(board, "T-003")
    assert child["status"] == "open"
    shown = run(tool, board, "show", "T-003", agent="bob")
    assert shown.returncode == 0, shown.stderr + shown.stdout
    handoff = shown.stdout
    assert "Handoff from dependencies" in handoff
    assert accepted_short in handoff
    assert accepted_full in handoff
    assert later_short not in handoff
    assert later_full not in handoff
    assert "main@%s" % later_short not in handoff
