"""T-377: run_start/run_end bind review-owned tickets, not only claimed."""

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


def test_run_start_binds_review_owned_ticket(board, tmp_path):
    b, repo = _worked(board)
    run(b, "next", "--role", "backend", agent="alice", cwd=repo)
    tid = "T-002"
    _commit(repo)
    run(b, "review", tid, "--notes", "paths tests", agent="alice", cwd=repo)
    run(b, "msg", "ping", "--to", "alice", agent="boss", cwd=repo)
    fake = _fake_harness(tmp_path, "echo ok\n")
    run(b, "watch", "--agent", "alice", "--once", "--exec", str(fake), "--cwd", str(repo),
        agent="alice", cwd=repo)
    start = events(b, kind="run_start")
    end = events(b, kind="run_end")
    assert len(start) == 1 and start[0].get("ticket") == tid
    assert len(end) == 1 and end[0].get("ticket") == tid


def test_run_start_claimed_unchanged(board, tmp_path):
    b, repo = _worked(board)
    run(b, "next", "--role", "backend", agent="alice", cwd=repo)
    tid = "T-002"
    fake = _fake_harness(tmp_path, "echo ok\n")
    run(b, "watch", "--agent", "alice", "--once", "--exec", str(fake), "--cwd", str(repo),
        agent="alice", cwd=repo)
    start = events(b, kind="run_start")[-1]
    assert start.get("ticket") == tid


def test_run_start_omits_ticket_when_idle(board, tmp_path):
    b, repo = _worked(board)
    run(b, "join", "idle", "--roles", "backend", agent="idle", cwd=repo)
    run(b, "msg", "hello", "--to", "idle", agent="boss", cwd=repo)
    fake = _fake_harness(tmp_path, "echo ok\n")
    run(b, "watch", "--agent", "idle", "--once", "--exec", str(fake), "--cwd", str(repo),
        agent="idle", cwd=repo)
    start = events(b, kind="run_start", agent="idle")
    assert len(start) == 1
    assert "ticket" not in start[0]


def test_turns_n_increases_for_review_owned_watch_pair(board, tmp_path):
    b, repo = _worked(board)
    run(b, "next", "--role", "backend", agent="alice", cwd=repo)
    tid = "T-002"
    _commit(repo)
    run(b, "review", tid, "--notes", "paths tests", agent="alice", cwd=repo)
    before = json.loads(run(b, "turns", "--json", cwd=repo).stdout)
    n_before = before["aggregates"]["n"]
    run(b, "msg", "ping", "--to", "alice", agent="boss", cwd=repo)
    fake = _fake_harness(tmp_path, "echo ok\n")
    run(b, "watch", "--agent", "alice", "--once", "--exec", str(fake), "--cwd", str(repo),
        agent="alice", cwd=repo)
    after = json.loads(run(b, "turns", "--json", cwd=repo).stdout)
    by = {r["ticket"]: r for r in after["tickets"]}
    assert by[tid]["turns"] == 1
    assert after["aggregates"]["n"] == n_before + 1
