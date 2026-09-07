"""T-425: idle IN-REVIEW watch pulses must not increment turns."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from test_trajectories import _fake_harness, events
from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _commit(repo, msg="work"):
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", msg],
                   check=True, env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


def test_idle_review_watch_once_pairs_turns_stay_at_claim(board, tmp_path, monkeypatch):
    """Throwaway board: claim run counts 1; two idle IN-REVIEW --once stay 1, not 3."""
    for var in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    repo = board.parent
    (repo / ".gitignore").write_text(".tickets/\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    _commit(repo, "ignore")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "alice/work"], check=True)
    run(board, "join", "alice", "--roles", "docs", "--tool", "claude", "--model", "opus",
        agent="alice", cwd=repo)
    # board fixture already created T-001 (docs). Claim it.
    run(board, "next", "--role", "docs", agent="alice", cwd=repo)
    tid = "T-001"
    update_sh = tmp_path / "update.sh"
    update_sh.write_text(
        "#!/bin/sh\n%s %s update %s working-this-run\n" % (sys.executable, TOOL, tid))
    update_sh.chmod(update_sh.stat().st_mode | stat.S_IEXEC)
    r = run(board, "watch", "--agent", "alice", "--once", "--exec", str(update_sh),
            "--cwd", str(repo), agent="alice", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    _commit(repo)
    run(board, "review", tid, "--notes", "paths tests", agent="alice", cwd=repo)
    mid = json.loads(run(board, "turns", "--json", cwd=repo).stdout)
    by = {row["ticket"]: row for row in mid["tickets"]}
    assert by[tid]["turns"] == 1
    n_before = mid["aggregates"]["n"]
    median_before = mid["aggregates"]["median"]

    idle = _fake_harness(tmp_path, "echo idle-pulse\n")
    for _ in range(2):
        run(board, "msg", "wake", "--to", "alice", agent="boss", cwd=repo)
        r = run(board, "watch", "--agent", "alice", "--once", "--exec", str(idle),
                "--cwd", str(repo), agent="alice", cwd=repo)
        assert r.returncode == 0, r.stdout + r.stderr

    after = json.loads(run(board, "turns", "--json", cwd=repo).stdout)
    by = {row["ticket"]: row for row in after["tickets"]}
    assert by[tid]["turns"] == 1
    assert after["aggregates"]["n"] == n_before
    assert after["aggregates"]["median"] == median_before
    assert after["v"] == 1
    assert set(after["tickets"][0]) == {
        "ticket", "owner", "model", "turns", "wall_clock_s", "reopens", "stuck", "outcome"}
    ends = events(board, kind="run_end", ticket=tid)
    assert len(ends) == 3
    assert ends[0].get("bound_write") is True
    assert "bound_write" not in ends[1]
    assert "bound_write" not in ends[2]
    assert ends[0].get("run_id") and ends[1].get("run_id") != ends[0].get("run_id")
