"""T-563: watch --once must omit self-owned IN REVIEW when mine is empty, without here."""

import os

from test_trajectories import _fake_harness, events
from test_t543_here_empty_review import _agent, _review_owned_empty_mine
from test_wakeup import board, run  # noqa: F401


def test_watch_once_omits_self_owned_review_without_here(board, tmp_path, monkeypatch):
    """agent.json still has ticket= after review; mine empty; no tickets here in-process."""
    for var in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    repo, tid = _review_owned_empty_mine(board)
    alice = _agent(board, "alice")
    assert alice.get("ticket") == tid

    run(board, "msg", "wake", "--to", "alice", agent="boss", cwd=repo)
    fake = _fake_harness(tmp_path, "echo ok\n")
    wr = run(board, "watch", "--agent", "alice", "--once", "--exec", str(fake),
             "--cwd", str(repo), agent="alice", cwd=repo)
    assert wr.returncode == 0, wr.stderr
    start = events(board, kind="run_start", agent="alice")
    assert start, "alice watch should still run (direct msg)"
    assert "ticket" not in start[-1] or start[-1].get("ticket") != tid
    assert _agent(board, "alice").get("ticket") == tid
