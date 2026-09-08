"""T-551: tickets here must not stamp an IN-REVIEW id owned by someone else."""

import json
import os
import subprocess
import sys
from pathlib import Path

from test_trajectories import _fake_harness, events
from test_wakeup import board, run  # noqa: F401

CLI = Path(__file__).resolve().parents[1] / "src" / "ticket_board" / "cli.py"


def _agent(board, name):
    return json.loads((board / "agents" / ("%s.json" % name)).read_text())


def _commit(repo, msg="work"):
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", msg],
        check=True,
        env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                 GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"),
    )


def _plant_ticket(board, name, tid):
    path = board / "agents" / ("%s.json" % name)
    rec = json.loads(path.read_text())
    rec["ticket"] = tid
    path.write_text(json.dumps(rec, indent=2))


def _alice_claimed_bob_review(board):
    """alice holds claimed T-001; bob owns IN REVIEW T-002."""
    repo = board.parent
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "alice/work"], check=True)
    (repo / ".gitignore").write_text(".tickets/\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    _commit(repo, "ignore")
    run(board, "join", "alice", "--roles", "docs", agent="alice", cwd=repo)
    run(board, "join", "bob", "--roles", "docs", agent="bob", cwd=repo)
    run(board, "next", "--role", "docs", agent="alice", cwd=repo)
    alice_tid = "T-001"
    run(board, "create", "bob work", "--role", "docs", cwd=repo)
    run(board, "next", "--role", "docs", agent="bob", cwd=repo)
    bob_tid = "T-002"
    _commit(repo, "bob work")
    rr = run(board, "review", bob_tid, "--notes", "paths tests", agent="bob", cwd=repo)
    assert rr.returncode == 0, rr.stderr
    assert _agent(board, "alice")["ticket"] == alice_tid
    return repo, alice_tid, bob_tid


def test_here_with_claimed_stamps_held_id_not_foreign_review(board, monkeypatch):
    for var in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    repo, alice_tid, bob_tid = _alice_claimed_bob_review(board)

    alice_cwd = _agent(board, "alice")["cwd"]
    alice_branch = _agent(board, "alice").get("branch")
    _plant_ticket(board, "alice", bob_tid)

    hr = run(board, "here", agent="alice", cwd=repo)
    assert hr.returncode == 0, hr.stderr
    alice = _agent(board, "alice")
    assert alice.get("ticket") == alice_tid
    assert alice["cwd"] == alice_cwd
    assert alice.get("branch") == alice_branch


def test_here_with_claimed_watch_omits_foreign_review(board, tmp_path, monkeypatch):
    for var in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    repo, alice_tid, bob_tid = _alice_claimed_bob_review(board)
    _plant_ticket(board, "alice", bob_tid)

    run(board, "here", agent="alice", cwd=repo)
    run(board, "msg", "wake", "--to", "alice", agent="boss", cwd=repo)
    fake = _fake_harness(tmp_path, "echo ok\n")
    wr = run(board, "watch", "--agent", "alice", "--once", "--exec", str(fake),
             "--cwd", str(repo), agent="alice", cwd=repo)
    assert wr.returncode == 0, wr.stderr
    start = events(board, kind="run_start", agent="alice")
    assert start, "alice watch should still run (direct msg)"
    assert start[-1].get("ticket") == alice_tid


def test_here_assign_not_claim_stamps_assigned_not_foreign_review(board, monkeypatch):
    """assign-not-claim: alice holds assigned T-002; bob IR T-003 must not bind."""
    for var in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    repo = board.parent
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "alice/work"], check=True)
    (repo / ".gitignore").write_text(".tickets/\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    _commit(repo, "ignore")
    run(board, "join", "alice", "--roles", "docs", agent="alice", cwd=repo)
    run(board, "join", "bob", "--roles", "docs", agent="bob", cwd=repo)
    run(board, "next", "--role", "docs", agent="alice", cwd=repo)
    stale = "T-001"
    _commit(repo, "alice work")
    run(board, "review", stale, "--notes", "paths tests", agent="alice", cwd=repo)
    run(board, "create", "bob work", "--role", "docs", cwd=repo)
    run(board, "next", "--role", "docs", agent="bob", cwd=repo)
    bob_tid = "T-002"
    _commit(repo, "bob work")
    run(board, "review", bob_tid, "--notes", "paths tests", agent="bob", cwd=repo)
    run(board, "create", "Assigned later", "--role", "docs", cwd=repo)
    assigned = "T-003"
    ar = run(board, "assign", assigned, "--owner", "alice", cwd=repo)
    assert ar.returncode == 0, ar.stderr
    _plant_ticket(board, "alice", bob_tid)

    hr = run(board, "here", agent="alice", cwd=repo)
    assert hr.returncode == 0, hr.stderr
    assert _agent(board, "alice").get("ticket") == assigned


def test_packaged_here_with_claimed_stamps_held_id_not_foreign_review(board):
    import subprocess as sp
    repo, alice_tid, bob_tid = _alice_claimed_bob_review(board)
    env = dict(os.environ, TICKETS_DIR=str(board), PYTHONPATH=str(CLI.parent.parent))
    env.pop("TICKETS_STOP_HOOK", None)

    def cli(*args, agent=""):
        e = dict(env, TICKET_AGENT=agent)
        return sp.run([sys.executable, str(CLI), *args], capture_output=True,
                      text=True, env=e, cwd=repo)

    _plant_ticket(board, "alice", bob_tid)
    hr = cli("here", agent="alice")
    assert hr.returncode == 0, hr.stderr
    alice = _agent(board, "alice")
    assert alice.get("ticket") == alice_tid
