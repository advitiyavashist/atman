"""T-425: ordinary DM after IN REVIEW must not wake or increment turns."""

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
    """Claim run counts 1; ordinary DMs after IN REVIEW do not start watch runs."""
    for var in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    repo = board.parent
    (repo / ".gitignore").write_text(".tickets/\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    _commit(repo, "ignore")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "alice/work"], check=True)
    run(board, "join", "alice", "--roles", "docs", "--tool", "claude", "--model", "opus",
        agent="alice", cwd=repo)
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
    starts_after_claim = len(events(board, kind="run_start"))

    idle = _fake_harness(tmp_path, "echo idle-pulse\n")
    for _ in range(2):
        run(board, "msg", "wake", "--to", "alice", agent="boss", cwd=repo)
        r = run(board, "watch", "--agent", "alice", "--once", "--exec", str(idle),
                "--cwd", str(repo), agent="alice", cwd=repo)
        assert r.returncode == 1, r.stdout + r.stderr

    fail_dir = tmp_path / "failh"
    fail_dir.mkdir()
    fail = _fake_harness(fail_dir, "exit 1\n")
    run(board, "msg", "wake-fail", "--to", "alice", agent="boss", cwd=repo)
    r = run(board, "watch", "--agent", "alice", "--once", "--exec", str(fail),
            "--cwd", str(repo), agent="alice", cwd=repo)
    assert r.returncode == 1, r.stdout + r.stderr

    after = json.loads(run(board, "turns", "--json", cwd=repo).stdout)
    by = {row["ticket"]: row for row in after["tickets"]}
    assert by[tid]["turns"] == 1
    assert after["aggregates"]["n"] == n_before
    assert after["aggregates"]["median"] == median_before
    assert after["v"] == 1
    from ticket_board.turns import ROW_KEYS
    assert tuple(after["tickets"][0].keys()) == ROW_KEYS
    assert len(events(board, kind="run_start")) == starts_after_claim
    ends = events(board, kind="run_end", ticket=tid)
    assert len(ends) == 1
    assert ends[0].get("bound_write") is True
