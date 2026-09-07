"""T-254: done must preserve the repository provenance of Git evidence."""
import json

import pytest

from test_wakeup import board, run  # noqa: F401
from test_merge_repo_identity import _create, _fetch_commit, _git, _ignore_board, _ticket


def _reviewed(board):
    repo = board.parent
    _ignore_board(repo)
    _git(repo, "checkout", "-b", "alice/work")
    tid = _create(board, "Repository-scoped work")
    assert run(board, "claim", tid, agent="alice").returncode == 0
    result = run(board, "review", tid, "--notes", "reviewed", agent="alice")
    assert result.returncode == 0, result.stderr
    return tid


@pytest.mark.parametrize("force", [False, True])
def test_done_rejects_other_repo_even_with_identical_sha(board, force):
    tid = _reviewed(board)
    before = _ticket(board, tid)
    other = board.parent.parent / "other"
    other.mkdir()
    _git(other, "init", "-q", "-b", "main")
    sha = _git(board.parent, "rev-parse", "HEAD")
    _fetch_commit(board.parent, other, sha)
    _git(other, "checkout", "-b", "alice/work", "main")
    assert _git(other, "rev-parse", "HEAD") == sha
    result = run(board, "done", tid, "--notes", "wrong checkout",
                 *(["--force"] if force else []), agent="ceo", cwd=other)
    assert result.returncode != 0
    assert "does not match" in result.stderr
    assert tid in result.stderr
    assert _ticket(board, tid) == before


def test_done_records_repo_without_prior_review(board):
    repo = board.parent
    _ignore_board(repo)
    _git(repo, "checkout", "-b", "alice/direct")
    tid = _create(board, "Direct completion")
    result = run(board, "done", tid, "--notes", "complete", agent="alice")
    assert result.returncode == 0, result.stderr
    rec = _ticket(board, tid)
    assert rec["status"] == "done"
    assert rec["repo"] == str((repo / ".git").resolve())
    assert rec["commit"] == "alice/direct@" + _git(repo, "rev-parse", "--short", "HEAD")


def test_done_accepts_different_worktree_of_reviewed_repo(board):
    tid = _reviewed(board)
    before = _ticket(board, tid)
    other = board.parent.parent / "integration"
    _git(board.parent, "worktree", "add", "-b", "integration", str(other))
    result = run(board, "done", tid, "--notes", "integrated", agent="ceo", cwd=other)
    assert result.returncode == 0, result.stderr
    rec = _ticket(board, tid)
    assert rec["status"] == "done"
    assert rec["repo"] == before["repo"]
    assert rec["commit"].startswith("integration@")


def test_done_without_git_cannot_close_repository_evidence(board):
    tid = _reviewed(board)
    before = _ticket(board, tid)
    outside = board.parent.parent / "outside"
    outside.mkdir()
    result = run(board, "done", tid, "--notes", "no checkout", agent="ceo", cwd=outside)
    assert result.returncode != 0
    assert "repository" in result.stderr
    assert _ticket(board, tid) == before


def test_done_warns_for_legacy_pin_and_records_current_repo(board):
    tid = _reviewed(board)
    legacy = _ticket(board, tid)
    repo = legacy.pop("repo")
    # Model pre-T-215 persisted evidence in this disposable fixture only.
    (board / (tid + ".json")).write_text(json.dumps(legacy))
    result = run(board, "done", tid, "--notes", "legacy completion", agent="ceo")
    assert result.returncode == 0, result.stderr
    assert tid in result.stderr
    assert "provenance cannot be verified" in result.stderr
    assert _ticket(board, tid)["repo"] == repo


def test_done_without_git_allows_ticket_without_git_evidence(board):
    outside = board.parent.parent / "outside"
    outside.mkdir()
    tid = _create(board, "Board-only decision")
    result = run(board, "done", tid, "--notes", "decision recorded", agent="ceo", cwd=outside)
    assert result.returncode == 0, result.stderr
    rec = _ticket(board, tid)
    assert rec["status"] == "done"
    assert not rec.get("repo")
    assert not rec.get("commit")
