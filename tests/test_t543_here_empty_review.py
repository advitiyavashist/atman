"""T-543: tickets here with mine=empty must not stamp an IN-REVIEW id the agent still owns."""

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


def _review_owned_empty_mine(board):
    """alice owns IN REVIEW T-001 and holds nothing claimed (mine is empty)."""
    repo = board.parent
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "alice/work"], check=True)
    (repo / ".gitignore").write_text(".tickets/\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    _commit(repo, "ignore")
    run(board, "join", "alice", "--roles", "docs", agent="alice", cwd=repo)
    run(board, "next", "--role", "docs", agent="alice", cwd=repo)
    tid = "T-001"
    _commit(repo, "work")
    rr = run(board, "review", tid, "--notes", "paths tests", agent="alice", cwd=repo)
    assert rr.returncode == 0, rr.stderr
    mine = run(board, "mine", agent="alice", cwd=repo)
    assert mine.returncode == 0, mine.stderr
    assert "T-001" not in (mine.stdout or "")
    return repo, tid


def test_here_with_empty_mine_clears_review_ticket_and_watch_omits(board, tmp_path, monkeypatch):
    for var in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    repo, tid = _review_owned_empty_mine(board)

    before = _agent(board, "alice")
    alice_cwd = before["cwd"]
    alice_branch = before.get("branch")
    alice_sha = before.get("sha")

    hr = run(board, "here", agent="alice", cwd=repo)
    assert hr.returncode == 0, hr.stderr
    alice = _agent(board, "alice")
    assert alice.get("ticket") in ("", None)
    assert alice["cwd"] == alice_cwd
    assert alice.get("branch") == alice_branch
    assert alice.get("sha") == alice_sha

    run(board, "msg", "wake", "--to", "alice", agent="boss", cwd=repo)
    fake = _fake_harness(tmp_path, "echo ok\n")
    wr = run(board, "watch", "--agent", "alice", "--once", "--force", "--exec", str(fake),
             "--cwd", str(repo), agent="alice", cwd=repo)
    assert wr.returncode == 0, wr.stderr
    start = events(board, kind="run_start", agent="alice")
    assert start, "alice watch should still run (direct msg)"
    assert "ticket" not in start[-1] or start[-1].get("ticket") != tid


def test_here_keeps_in_progress_ticket(board, monkeypatch):
    """T-377 claimed path: here must still stamp the IN PROGRESS id."""
    for var in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    repo = board.parent
    run(board, "join", "alice", "--roles", "docs", agent="alice", cwd=repo)
    run(board, "next", "--role", "docs", agent="alice", cwd=repo)
    tid = "T-001"
    assert _agent(board, "alice")["ticket"] == tid
    hr = run(board, "here", agent="alice", cwd=repo)
    assert hr.returncode == 0, hr.stderr
    alice = _agent(board, "alice")
    assert alice.get("ticket") == tid


def test_packaged_here_with_empty_mine_clears_review_ticket(board):
    import subprocess as sp
    repo = board.parent
    env = dict(os.environ, TICKETS_DIR=str(board), PYTHONPATH=str(CLI.parent.parent))
    env.pop("TICKETS_STOP_HOOK", None)

    def cli(*args, agent=""):
        e = dict(env, TICKET_AGENT=agent)
        return sp.run([sys.executable, str(CLI), *args], capture_output=True,
                      text=True, env=e, cwd=repo)

    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "alice/work"], check=True)
    (repo / ".gitignore").write_text(".tickets/\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    _commit(repo, "ignore")
    assert cli("join", "alice", "--roles", "docs", agent="alice").returncode == 0
    assert cli("next", "--role", "docs", agent="alice").returncode == 0
    _commit(repo, "work")
    rr = cli("review", "T-001", "--notes", "paths tests", agent="alice")
    assert rr.returncode == 0, rr.stderr
    before = _agent(board, "alice")
    hr = cli("here", agent="alice")
    assert hr.returncode == 0, hr.stderr
    alice = _agent(board, "alice")
    assert alice.get("ticket") in ("", None)
    assert alice["cwd"] == before["cwd"]
    assert alice.get("branch") == before.get("branch")
    assert alice.get("sha") == before.get("sha")
