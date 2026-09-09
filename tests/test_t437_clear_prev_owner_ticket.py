"""T-437: claim/assign clears the previous owner's agents/<name>.json ticket=."""

import json
import os
from pathlib import Path

from test_trajectories import _fake_harness, events
from test_wakeup import board, run  # noqa: F401

CLI = Path(__file__).resolve().parents[1] / "src" / "ticket_board" / "cli.py"


def _agent(board, name):
    return json.loads((board / "agents" / ("%s.json" % name)).read_text())


def test_assign_clears_previous_owner_ticket_and_watch_omits(board, tmp_path, monkeypatch):
    for var in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    repo = board.parent
    run(board, "join", "alice", "--roles", "docs", agent="alice", cwd=repo)
    run(board, "join", "bob", "--roles", "docs", agent="bob", cwd=repo)
    run(board, "next", "--role", "docs", agent="alice", cwd=repo)
    tid = "T-001"
    alice_cwd = _agent(board, "alice")["cwd"]
    alice_branch = _agent(board, "alice").get("branch")
    assert _agent(board, "alice")["ticket"] == tid

    r = run(board, "assign", tid, "--owner", "bob", cwd=repo)
    assert r.returncode == 0, r.stderr
    alice = _agent(board, "alice")
    bob = _agent(board, "bob")
    assert alice.get("ticket") in ("", None)
    assert bob.get("ticket") == tid
    assert alice["cwd"] == alice_cwd
    assert alice.get("branch") == alice_branch

    run(board, "msg", "wake", "--to", "alice", agent="boss", cwd=repo)
    fake = _fake_harness(tmp_path, "echo ok\n")
    run(board, "watch", "--agent", "alice", "--once", "--force", "--exec", str(fake),
        "--cwd", str(repo), agent="alice", cwd=repo)
    start = events(board, kind="run_start", agent="alice")
    assert start, "alice watch should still run (direct msg)"
    assert "ticket" not in start[-1] or start[-1].get("ticket") != tid


def test_packaged_assign_clears_previous_owner_ticket(board):
    import subprocess
    import sys
    repo = board.parent
    env = dict(os.environ, TICKETS_DIR=str(board), PYTHONPATH=str(CLI.parent.parent))
    env.pop("TICKETS_STOP_HOOK", None)

    def cli(*args, agent=""):
        e = dict(env, TICKET_AGENT=agent)
        return subprocess.run([sys.executable, str(CLI), *args], capture_output=True,
                              text=True, env=e, cwd=repo)

    assert cli("join", "alice", "--roles", "docs", agent="alice").returncode == 0
    assert cli("join", "bob", "--roles", "docs", agent="bob").returncode == 0
    assert cli("next", "--role", "docs", agent="alice").returncode == 0
    assert _agent(board, "alice")["ticket"] == "T-001"
    r = cli("assign", "T-001", "--owner", "bob")
    assert r.returncode == 0, r.stderr
    assert _agent(board, "alice").get("ticket") in ("", None)
    assert _agent(board, "bob").get("ticket") == "T-001"
