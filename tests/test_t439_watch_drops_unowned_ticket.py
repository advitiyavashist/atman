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
    wr = run(board, "watch", "--agent", "alice", "--once", "--force", "--exec", str(fake),
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


def test_watch_once_drops_stale_review_bind_after_assign_not_claim(board, tmp_path, monkeypatch):
    """composer.json ticket=T-438 while planner assigned T-415: watch must not bind the review id."""
    import subprocess
    for var in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    repo = board.parent
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "alice/work"], check=True)
    (repo / ".gitignore").write_text(".tickets/\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "ignore"],
        check=True,
        env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                 GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"),
    )
    run(board, "join", "alice", "--roles", "docs", agent="alice", cwd=repo)
    run(board, "next", "--role", "docs", agent="alice", cwd=repo)
    stale = "T-001"
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "work"],
        check=True,
        env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                 GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"),
    )
    rr = run(board, "review", stale, "--notes", "paths tests", agent="alice", cwd=repo)
    assert rr.returncode == 0, rr.stderr
    cr = run(board, "create", "Assigned later", "--role", "docs", cwd=repo)
    assert cr.returncode == 0, cr.stderr
    assigned = "T-002"
    ar = run(board, "assign", assigned, "--owner", "alice", cwd=repo)
    assert ar.returncode == 0, ar.stderr
    assert _agent(board, "alice")["ticket"] == assigned

    alice_cwd = _agent(board, "alice")["cwd"]
    alice_branch = _agent(board, "alice").get("branch")
    alice_sha = _agent(board, "alice").get("sha")
    _plant_ticket(board, "alice", stale)
    assert _agent(board, "alice")["ticket"] == stale

    run(board, "msg", "wake", "--to", "alice", agent="boss", cwd=repo)
    fake = _fake_harness(tmp_path, "echo ok\n")
    wr = run(board, "watch", "--agent", "alice", "--once", "--exec", str(fake),
             "--cwd", str(repo), agent="alice", cwd=repo)
    assert wr.returncode == 0, wr.stderr

    alice = _agent(board, "alice")
    assert alice.get("ticket") != stale
    assert alice["cwd"] == alice_cwd
    assert alice.get("branch") == alice_branch
    assert alice.get("sha") == alice_sha

    start = events(board, kind="run_start", agent="alice")
    assert start, "alice watch should still run (direct msg)"
    assert start[-1].get("ticket") == assigned
    assert start[-1].get("ticket") != stale


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


def test_packaged_checkin_drops_stale_review_bind_after_assign(board):
    import subprocess
    import sys
    repo = board.parent
    env = dict(os.environ, TICKETS_DIR=str(board), PYTHONPATH=str(CLI.parent.parent))
    env.pop("TICKETS_STOP_HOOK", None)

    def cli(*args, agent=""):
        e = dict(env, TICKET_AGENT=agent)
        return subprocess.run([sys.executable, str(CLI), *args], capture_output=True,
                              text=True, env=e, cwd=repo)

    def root(*args, agent=""):
        e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent)
        e.pop("TICKETS_STOP_HOOK", None)
        tool = Path(__file__).resolve().parents[1] / "tickets.py"
        return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                              text=True, env=e, cwd=repo)

    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "alice/work"], check=True)
    (repo / ".gitignore").write_text(".tickets/\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "ignore"],
        check=True,
        env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                 GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"),
    )
    assert cli("join", "alice", "--roles", "docs", agent="alice").returncode == 0
    assert root("next", "--role", "docs", agent="alice").returncode == 0
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "work"],
        check=True,
        env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                 GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"),
    )
    assert root("review", "T-001", "--notes", "paths tests", agent="alice").returncode == 0
    assert root("create", "Assigned later", "--role", "docs").returncode == 0
    assert root("assign", "T-002", "--owner", "alice").returncode == 0
    _plant_ticket(board, "alice", "T-001")
    r = cli("join", "alice", "--roles", "docs", agent="alice")
    assert r.returncode == 0, r.stderr
    assert _agent(board, "alice").get("ticket") != "T-001"
