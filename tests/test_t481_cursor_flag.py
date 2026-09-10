"""T-481: FLAG pairing key is run_id else (agent, run_no) for Cursor-harness rows."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from test_trajectories import _fake_harness, events
from test_wakeup import board, run  # noqa: F401
from ticket_board.turns import ROW_KEYS

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _commit(repo, msg="work"):
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", msg],
                   check=True, env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


def test_cursor_shaped_fixture_board_idle_stays_one(board):
    """Throwaway board, not live jsonl: Cursor-shaped rows, two idle pulses stay 1."""
    repo = board.parent
    lines = [
        {"kind": "claim", "ticket": "T-001", "agent": "cursor-demo", "run_no": 1,
         "at": "2026-09-08T00:00:00Z"},
        {"kind": "run_start", "ticket": "T-001", "agent": "cursor-demo",
         "run_no": 1, "at": "2026-09-08T00:00:00Z"},
        {"kind": "update", "ticket": "T-001", "agent": "cursor-demo", "run_no": 1,
         "at": "2026-09-08T00:00:30Z"},
        {"kind": "run_end", "ticket": "T-001", "agent": "cursor-demo",
         "run_no": 1, "exit": 0, "at": "2026-09-08T00:01:00Z"},
        {"kind": "review", "ticket": "T-001", "agent": "cursor-demo", "run_no": 1,
         "at": "2026-09-08T00:01:05Z"},
        {"kind": "run_start", "ticket": "T-001", "agent": "cursor-demo",
         "run_no": 2, "at": "2026-09-08T00:02:00Z"},
        {"kind": "run_end", "ticket": "T-001", "agent": "cursor-demo",
         "run_no": 2, "exit": 0, "at": "2026-09-08T00:02:20Z"},
        {"kind": "run_start", "ticket": "T-001", "agent": "cursor-demo",
         "run_no": 3, "at": "2026-09-08T00:03:00Z"},
        {"kind": "run_end", "ticket": "T-001", "agent": "cursor-demo",
         "run_no": 3, "exit": 0, "at": "2026-09-08T00:03:20Z"},
    ]
    (board / "trajectories.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in lines))
    t = json.loads((board / "T-001.json").read_text())
    t.update({"owner": "cursor-demo", "status": "review", "role": "docs"})
    (board / "T-001.json").write_text(json.dumps(t, indent=2))
    mid = json.loads(run(board, "turns", "--json", cwd=repo).stdout)
    by = {row["ticket"]: row for row in mid["tickets"]}
    assert by["T-001"]["turns"] == 1
    assert mid["aggregates"]["n"] == 1
    assert mid["v"] == 1
    assert tuple(mid["tickets"][0].keys()) == ROW_KEYS

    extra = [
        {"kind": "update", "ticket": "T-001", "agent": "cursor-demo", "run_no": 4,
         "at": "2026-09-08T00:04:00Z"},
        {"kind": "run_end", "ticket": "T-001", "agent": "cursor-demo",
         "run_no": 4, "exit": 0, "at": "2026-09-08T00:04:10Z"},
    ]
    with (board / "trajectories.jsonl").open("a") as f:
        for e in extra:
            f.write(json.dumps(e) + "\n")
    after = json.loads(run(board, "turns", "--json", cwd=repo).stdout)
    by = {row["ticket"]: row for row in after["tickets"]}
    assert by["T-001"]["turns"] == 2


def test_cursor_tool_watch_stamps_run_id_and_bound_write(board, tmp_path, monkeypatch):
    """Cursor-harness watch child gets the same TICKETS_RUN_ID FLAG as Claude.

    After IN REVIEW, idle --task watches still stamp distinct run_ids; they do
    not bind the reviewed ticket and do not increment turns.
    """
    for var in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    repo = board.parent
    (repo / ".gitignore").write_text(".tickets/\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    _commit(repo, "ignore")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "alice/work"], check=True)
    run(board, "join", "alice", "--roles", "docs", "--tool", "cursor", "--model", "grok",
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
    idle = _fake_harness(tmp_path, "echo idle-pulse\n")
    for _ in range(2):
        # Task-only seats (Cursor workers included) ignore ordinary DMs after
        # IN REVIEW (T-425). An explicit --task is the wake that starts an idle
        # FLAG run without a bound write.
        run(board, "msg", "idle-pulse", "--task", "--to", "alice", agent="boss", cwd=repo)
        r = run(board, "watch", "--agent", "alice", "--once", "--exec", str(idle),
                "--cwd", str(repo), agent="alice", cwd=repo)
        assert r.returncode == 0, r.stdout + r.stderr
    after = json.loads(run(board, "turns", "--json", cwd=repo).stdout)
    by = {row["ticket"]: row for row in after["tickets"]}
    assert by[tid]["turns"] == 1
    # Idle pulses must not bind the IN REVIEW ticket (T-543/T-563); FLAG
    # pairing is run_id on the claim run vs later watch runs.
    claimed = events(board, kind="run_end", ticket=tid)
    assert len(claimed) == 1
    assert claimed[0].get("bound_write") is True
    assert claimed[0].get("run_id")
    assert claimed[0].get("run_no") == 1
    alice_ends = events(board, kind="run_end", agent="alice")
    assert len(alice_ends) == 3
    idle_ends = [e for e in alice_ends if e.get("ticket") != tid]
    assert len(idle_ends) == 2
    assert all("bound_write" not in e for e in idle_ends)
    assert idle_ends[0].get("run_id") and idle_ends[1].get("run_id")
    assert idle_ends[0]["run_id"] != idle_ends[1].get("run_id")
    assert idle_ends[0]["run_id"] != claimed[0]["run_id"]
    writes = events(board, kind="update", ticket=tid)
    assert writes and writes[0].get("run_id") == claimed[0].get("run_id")
    assert writes[0].get("run_no") == 1
