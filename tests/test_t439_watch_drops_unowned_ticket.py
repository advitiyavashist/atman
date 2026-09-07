"""T-439: watch/spawn start drops agents/<me>.json ticket= when someone else owns it."""

import json
import os
from pathlib import Path

from test_trajectories import _fake_harness, events
from test_wakeup import board, run  # noqa: F401

CLI = Path(__file__).resolve().parents[1] / "src" / "ticket_board" / "cli.py"


def _agent(board, name):
    return json.loads((board / "agents" / ("%s.json" % name)).read_text())


def _plant_ticket(board, name, tid):
    path = board / "agents" / ("%s.json" % name)
    rec = json.loads(path.read_text())
    rec["ticket"] = tid
    path.write_text(json.dumps(rec, indent=2))


def test_watch_once_drops_stale_ticket_and_omits_run_start(board, tmp_path, monkeypatch):
    for var in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    repo = board.parent
    run(board, "join", "alice", "--roles", "docs", agent="alice", cwd=repo)
    run(board, "join", "bob", "--roles", "docs", agent="bob", cwd=repo)
    run(board, "next", "--role", "docs", agent="alice", cwd=repo)
    tid = "T-001"
    r = run(board, "assign", tid, "--owner", "bob", cwd=repo)
    assert r.returncode == 0, r.stderr
    assert _agent(board, "bob")["ticket"] == tid

    alice_cwd = _agent(board, "alice")["cwd"]
    alice_branch = _agent(board, "alice").get("branch")
    alice_sha = _agent(board, "alice").get("sha")
    _plant_ticket(board, "alice", tid)
    assert _agent(board, "alice")["ticket"] == tid

    run(board, "msg", "wake", "--to", "alice", agent="boss", cwd=repo)
    fake = _fake_harness(tmp_path, "echo ok\n")
    wr = run(board, "watch", "--agent", "alice", "--once", "--exec", str(fake),
             "--cwd", str(repo), agent="alice", cwd=repo)
    assert wr.returncode == 0, wr.stderr

    alice = _agent(board, "alice")
    assert alice.get("ticket") in ("", None)
    assert alice["cwd"] == alice_cwd
    assert alice.get("branch") == alice_branch
    assert alice.get("sha") == alice_sha

    start = events(board, kind="run_start", agent="alice")
    assert start, "alice watch should still run (direct msg)"
    assert "ticket" not in start[-1] or start[-1].get("ticket") != tid


def test_packaged_checkin_drops_unowned_ticket(board):
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
    assert cli("assign", "T-001", "--owner", "bob").returncode == 0
    _plant_ticket(board, "alice", "T-001")
    assert _agent(board, "alice")["ticket"] == "T-001"
    r = cli("join", "alice", "--roles", "docs", agent="alice")
    assert r.returncode == 0, r.stderr
    assert _agent(board, "alice").get("ticket") in ("", None)
    assert _agent(board, "bob").get("ticket") == "T-001"
