"""T-423: cmd_sync must compare against origin/<trunk>, not a stale local main ref."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
GIT_ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="t",
    GIT_AUTHOR_EMAIL="t@t",
    GIT_COMMITTER_NAME="t",
    GIT_COMMITTER_EMAIL="t@t",
)


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=GIT_ENV)


def run_sync(cwd, entry="root", board=None):
    e = dict(os.environ, TICKET_AGENT="sync-agent")
    e.pop("TICKETS_STOP_HOOK", None)
    e.pop("TICKETS_DIR", None)
    if board:
        e["TICKETS_DIR"] = str(board)
    if entry == "pkg":
        e["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT)])
        cmd = [sys.executable, "-m", "ticket_board", "sync"]
    else:
        cmd = [sys.executable, str(TOOL), "sync"]
    return subprocess.run(cmd, capture_output=True, text=True, env=e, cwd=str(cwd))


@pytest.fixture
def stale_main_fixture(tmp_path):
    """Local main behind origin/main; feature branch contains local main but not origin/main."""
    bare = tmp_path / "remote.git"
    git("init", "--bare", str(bare))
    clone = tmp_path / "clone"
    git("clone", str(bare), str(clone))
    git("checkout", "-q", "-b", "main", cwd=clone)
    git("commit", "-q", "--allow-empty", "-m", "main-a", cwd=clone)
    main_a = git("rev-parse", "HEAD", cwd=clone).stdout.strip()
    git("checkout", "-q", "-b", "feature", cwd=clone)
    git("checkout", "-q", "main", cwd=clone)
    git("commit", "-q", "--allow-empty", "-m", "main-b", cwd=clone)
    main_b = git("rev-parse", "HEAD", cwd=clone).stdout.strip()
    git("push", "-u", "origin", "main", cwd=clone)
    git("checkout", "-q", "feature", cwd=clone)
    git("update-ref", "refs/heads/main", main_a, cwd=clone)
    board = tmp_path / "board" / ".tickets"
    board.mkdir(parents=True)
    assert git("merge-base", "--is-ancestor", "main", "HEAD", cwd=clone).returncode == 0
    assert git("merge-base", "--is-ancestor", "origin/main", "HEAD", cwd=clone).returncode != 0
    return {"clone": clone, "main_a": main_a, "main_b": main_b, "board": board}


@pytest.mark.parametrize("entry", ["root", "pkg"])
def test_sync_merges_origin_main_when_local_main_is_stale(stale_main_fixture, entry):
    clone = stale_main_fixture["clone"]
    main_b = stale_main_fixture["main_b"]
    board = stale_main_fixture["board"]
    r = run_sync(clone, entry=entry, board=board)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "origin/main" in r.stdout
    assert "merged origin/main" in r.stdout
    assert git("merge-base", "--is-ancestor", main_b, "HEAD", cwd=clone).returncode == 0


@pytest.mark.parametrize("entry", ["root", "pkg"])
def test_sync_noops_when_branch_contains_origin_main(stale_main_fixture, entry):
    clone = stale_main_fixture["clone"]
    board = stale_main_fixture["board"]
    assert run_sync(clone, entry=entry, board=board).returncode == 0
    r = run_sync(clone, entry=entry, board=board)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "already contains origin/main; nothing to do" in r.stdout


def test_sync_refuses_when_origin_fetch_fails(stale_main_fixture):
    clone = stale_main_fixture["clone"]
    board = stale_main_fixture["board"]
    git("remote", "rename", "origin", "upstream", cwd=clone)
    r = run_sync(clone, board=board)
    assert r.returncode != 0
    assert "no 'origin' remote" in r.stderr + r.stdout


def test_sync_refuses_unreachable_origin(stale_main_fixture, monkeypatch):
    clone = stale_main_fixture["clone"]
    board = stale_main_fixture["board"]
    git("remote", "set-url", "origin", str(clone.parent / "missing.git"), cwd=clone)
    r = run_sync(clone, board=board)
    assert r.returncode != 0
    assert "could not fetch origin/main" in r.stderr + r.stdout
    assert "nothing to do" not in r.stdout
