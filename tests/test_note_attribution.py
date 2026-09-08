"""T-238: a note's `by` field must be the writer's identity, not the ticket
owner's.

Runs the real CLI as a subprocess against a throwaway board, same pattern as
test_wakeup.py, so this exercises exactly what `tickets show` / `tickets
update` return to an agent -- not the in-process functions.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def run(board, *args, agent="", cwd=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    where = cwd or (board.parent if board.parent.is_dir() else Path("/"))
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                          text=True, env=e, cwd=where)


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    b = repo / ".tickets"
    r = run(b, "create", "Shared work", "--role", "backend", cwd=repo)
    assert r.returncode == 0, r.stderr
    return b


def test_a_note_from_a_non_owner_is_attributed_to_the_writer_not_the_owner(board):
    """The T-225 scenario: agent-a claims and owns the ticket; agent-b posts
    a progress note on it anyway (a second lane working the same ticket in
    parallel). `tickets show` must attribute agent-b's note to agent-b."""
    assert run(board, "next", agent="agent-a").returncode == 0
    r = run(board, "update", "T-001", "working on it", agent="agent-b")
    assert r.returncode == 0, r.stderr
    shown = run(board, "show", "T-001", agent="agent-a").stdout
    assert "[agent-b] working on it" in shown
    assert "[agent-a] working on it" not in shown


def test_a_note_from_a_non_owner_notifies_the_owner(board):
    """Ask #5: correct attribution alone only helps a reader who goes
    looking. A stranger's note on a claimed ticket must actively signal the
    owner, since silent duplicate work is the actual harm."""
    assert run(board, "next", agent="agent-a").returncode == 0
    # T-244 stamps agent-a's inbox_seen the moment its record is created here
    # (the "next" claim above is its first-ever check-in); without a real gap,
    # agent-b's note below can land in the SAME wall-clock second, which the
    # pre-existing, separately-filed T-228 hole (strict `>` on second-resolution
    # timestamps) would then hide. Not this test's bug to fix -- just avoid it.
    time.sleep(1.1)
    r = run(board, "update", "T-001", "also working on this", agent="agent-b")
    assert r.returncode == 0, r.stderr
    assert "agent-a" in r.stdout  # the printed warning names the owner
    inbox = run(board, "inbox", agent="agent-a").stdout
    assert "T-001" in inbox and "agent-b" in inbox


def test_a_note_from_the_owner_does_not_spuriously_warn(board):
    assert run(board, "next", agent="agent-a").returncode == 0
    r = run(board, "update", "T-001", "on track", agent="agent-a")
    assert r.returncode == 0, r.stderr
    assert "warning" not in r.stdout


def test_block_note_is_attributed_to_the_caller_not_the_owner(board):
    assert run(board, "next", agent="agent-a").returncode == 0
    r = run(board, "block", "T-001", "--reason", "waiting on X", agent="agent-b")
    assert r.returncode == 0, r.stderr
    shown = run(board, "show", "T-001", agent="agent-a").stdout
    assert "[agent-b] waiting on X" in shown


def test_review_note_is_attributed_to_the_submitter_not_the_recorded_owner(board):
    assert run(board, "next", agent="agent-a").returncode == 0
    r = run(board, "review", "T-001", "--notes", "done, see tests", "--force",
            agent="agent-b")
    assert r.returncode == 0, r.stderr
    shown = run(board, "show", "T-001", agent="agent-a").stdout
    assert "[agent-b]" in shown
    assert "REVIEW: " in shown
