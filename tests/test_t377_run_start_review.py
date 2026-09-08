"""T-377: run_start/run_end bind claimed work; leftover IN REVIEW is not an idle wake."""

import json
import os
import subprocess
from pathlib import Path

from test_trajectories import _fake_harness, events
from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _commit(repo, msg="work"):
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", msg],
                   check=True, env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


def _worked(board):
    repo = board.parent
    (repo / ".gitignore").write_text(".tickets/\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    _commit(repo, "ignore")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "alice/work"], check=True)
    run(board, "join", "alice", "--roles", "backend", "--tool", "claude", "--model", "opus",
        agent="alice", cwd=repo)
    run(board, "create", "Ship it", "--role", "backend", cwd=repo)
    return board, repo


def test_run_start_does_not_bind_leftover_review_on_ordinary_dm(board, tmp_path):
    """After review, mine is empty; a ping is notify-only and must not run_start."""
    b, repo = _worked(board)
    run(b, "next", "--role", "backend", agent="alice", cwd=repo)
    tid = "T-002"
    _commit(repo)
    run(b, "review", tid, "--notes", "paths tests", agent="alice", cwd=repo)
    run(b, "msg", "ping", "--to", "alice", agent="boss", cwd=repo)
    fake = _fake_harness(tmp_path, "echo ok\n")
    r = run(b, "watch", "--agent", "alice", "--once", "--exec", str(fake), "--cwd", str(repo),
            agent="alice", cwd=repo)
    assert r.returncode == 1, r.stdout + r.stderr
    start = events(b, kind="run_start")
    assert start == []


def test_run_start_claimed_unchanged(board, tmp_path):
    b, repo = _worked(board)
    run(b, "next", "--role", "backend", agent="alice", cwd=repo)
    tid = "T-002"
    fake = _fake_harness(tmp_path, "echo ok\n")
    run(b, "watch", "--agent", "alice", "--once", "--exec", str(fake), "--cwd", str(repo),
        agent="alice", cwd=repo)
    start = events(b, kind="run_start")[-1]
    assert start.get("ticket") == tid


def test_run_start_skips_ordinary_dm_when_idle(board, tmp_path):
    b, repo = _worked(board)
    run(b, "join", "idle", "--roles", "evals", agent="idle", cwd=repo)
    run(b, "msg", "hello", "--to", "idle", agent="boss", cwd=repo)
    fake = _fake_harness(tmp_path, "echo ok\n")
    r = run(b, "watch", "--agent", "idle", "--once", "--exec", str(fake), "--cwd", str(repo),
            agent="idle", cwd=repo)
    assert r.returncode == 1, r.stdout + r.stderr
    start = events(b, kind="run_start", agent="idle")
    assert start == []


def test_turns_n_unchanged_for_review_owned_ordinary_dm(board, tmp_path, monkeypatch):
    """Idle ping after review must not start a watch run or increment turns."""
    for var in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    b, repo = _worked(board)
    run(b, "next", "--role", "backend", agent="alice", cwd=repo)
    tid = "T-002"
    _commit(repo)
    run(b, "review", tid, "--notes", "paths tests", agent="alice", cwd=repo)
    before = json.loads(run(b, "turns", "--json", cwd=repo).stdout)
    n_before = before["aggregates"]["n"]
    run(b, "msg", "ping", "--to", "alice", agent="boss", cwd=repo)
    fake = _fake_harness(tmp_path, "echo ok\n")
    r = run(b, "watch", "--agent", "alice", "--once", "--exec", str(fake), "--cwd", str(repo),
            agent="alice", cwd=repo)
    assert r.returncode == 1, r.stdout + r.stderr
    after = json.loads(run(b, "turns", "--json", cwd=repo).stdout)
    assert after["aggregates"]["n"] == n_before
    assert events(b, kind="run_start") == []
