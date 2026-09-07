"""T-428: a non-owner closer must not overwrite the owner's git location.

`tickets done` / `tickets review` used to `checkin()` the ticket's owner from
the closer's process cwd. That copies the closer's cwd/branch/sha/worktree onto
the owner's agents/<owner>.json, so `tickets who` then reports the owner sitting
in the closer's tree. Same attribution class as T-238 / T-215.

Two-copy: the live shim is tickets.py; pip install runs src/ticket_board/cli.py.
Both must keep the owner's location and stamp the closer's own record instead.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROOT_TOOL = ROOT / "tickets.py"
PKG_TOOL = ROOT / "src" / "ticket_board" / "cli.py"

GIT_ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="t",
    GIT_AUTHOR_EMAIL="t@t",
    GIT_COMMITTER_NAME="t",
    GIT_COMMITTER_EMAIL="t@t",
)


def _git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                       env=GIT_ENV, check=True)
    return r.stdout.strip()


def run_tool(tool, board, *args, agent="", cwd=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    where = cwd or (board.parent if board.parent.is_dir() else Path("/"))
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                          text=True, env=e, cwd=where)


def _agent(board, name):
    return json.loads((board / "agents" / ("%s.json" % name)).read_text())


@pytest.fixture
def trees(tmp_path):
    """Isolated board + two worktrees with distinct branch@sha."""
    repo = tmp_path / "alice-tree"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    _git(repo, "checkout", "-q", "-b", "alice/work")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "alice")
    alice_sha = _git(repo, "rev-parse", "--short", "HEAD")

    bob_tree = tmp_path / "bob-tree"
    _git(repo, "worktree", "add", "-q", "-b", "bob/work", str(bob_tree), "main")
    _git(bob_tree, "commit", "-q", "--allow-empty", "-m", "bob")
    bob_sha = _git(bob_tree, "rev-parse", "--short", "HEAD")

    board = repo / ".tickets"
    r = run_tool(ROOT_TOOL, board, "create", "Shared work", "--role", "backend", cwd=repo)
    assert r.returncode == 0, r.stderr
    assert run_tool(ROOT_TOOL, board, "join", "alice", "--roles", "backend", cwd=repo).returncode == 0
    assert run_tool(ROOT_TOOL, board, "join", "bob", "--roles", "backend", cwd=bob_tree).returncode == 0
    return {
        "board": board,
        "alice_tree": repo,
        "bob_tree": bob_tree,
        "alice_sha": alice_sha,
        "bob_sha": bob_sha,
    }


def _claim_as_alice(tool, trees):
    board, wa = trees["board"], trees["alice_tree"]
    r = run_tool(tool, board, "claim", "T-001", agent="alice", cwd=wa)
    assert r.returncode == 0, r.stderr
    rec = _agent(board, "alice")
    assert rec["cwd"] == str(wa)
    assert rec["branch"] == "alice/work"
    assert rec["sha"] == trees["alice_sha"]
    assert rec["worktree"] == str(wa)
    return rec


@pytest.mark.parametrize("tool", [ROOT_TOOL, PKG_TOOL], ids=["tickets.py", "cli.py"])
def test_done_by_non_owner_preserves_owner_git_state(tool, trees):
    board, wa, wb = trees["board"], trees["alice_tree"], trees["bob_tree"]
    before = _claim_as_alice(tool, trees)

    r = run_tool(tool, board, "done", "T-001", "--notes", "closed by bob",
                 agent="bob", cwd=wb)
    assert r.returncode == 0, r.stderr

    alice = _agent(board, "alice")
    bob = _agent(board, "bob")
    assert alice["cwd"] == before["cwd"] == str(wa)
    assert alice["branch"] == before["branch"] == "alice/work"
    assert alice["sha"] == before["sha"] == trees["alice_sha"]
    assert alice["worktree"] == before["worktree"] == str(wa)
    assert alice.get("ticket") in ("", None)
    assert "finished T-001" in (alice.get("note") or "")
    assert "bob" in (alice.get("note") or "")

    assert bob["cwd"] == str(wb)
    assert bob["branch"] == "bob/work"
    assert bob["sha"] == trees["bob_sha"]
    assert bob["worktree"] == str(wb)


@pytest.mark.parametrize("tool", [ROOT_TOOL, PKG_TOOL], ids=["tickets.py", "cli.py"])
def test_review_by_non_owner_preserves_owner_git_state(tool, trees):
    board, wa, wb = trees["board"], trees["alice_tree"], trees["bob_tree"]
    before = _claim_as_alice(tool, trees)

    r = run_tool(tool, board, "review", "T-001", "--notes", "looks done",
                 "--force", agent="bob", cwd=wb)
    assert r.returncode == 0, r.stderr

    alice = _agent(board, "alice")
    bob = _agent(board, "bob")
    assert alice["cwd"] == before["cwd"] == str(wa)
    assert alice["branch"] == before["branch"] == "alice/work"
    assert alice["sha"] == before["sha"] == trees["alice_sha"]
    assert alice["worktree"] == before["worktree"] == str(wa)

    assert bob["cwd"] == str(wb)
    assert bob["branch"] == "bob/work"
    assert bob["sha"] == trees["bob_sha"]
