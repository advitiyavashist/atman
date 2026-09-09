"""T-494: join must not false-REFUSE when a linked worktree has its own .tickets.

T-424's guard compared board_dir()'s TICKETS_DIR answer against
_join_cwd_board(), which uses _init_cwd_worktree_root(). Inside a linked
worktree that holds its own real .tickets, those disagree by construction
even when TICKETS_DIR names the board join would have used anyway (the main
worktree's shared board). The fix compares against the same resolver with
TICKETS_DIR stripped instead.

Same subprocess-CLI, two-copy pattern as test_t424_join_isolation.py.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]


def clean_env(tmp_path, **overrides):
    e = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path / "fake-home"),
        "PYTEST_CURRENT_TEST": "tests/test_t494_join_linked_worktree.py::simulated (call)",
    }
    for passthrough in ("TMPDIR", "TMP", "TEMP"):
        if os.environ.get(passthrough):
            e[passthrough] = os.environ[passthrough]
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    e.update(overrides)
    return e


def run(tool, cwd, *args, env=None, tmp_path=None):
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                          text=True, cwd=str(cwd), env=env or clean_env(tmp_path))


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t494@test", "-c", "user.name=t494", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("t494\n")
    git(path, "add", "README")
    git(path, "commit", "-qm", "init")
    return path


def make_linked_worktree(main_repo, wt_path):
    git(main_repo, "worktree", "add", "-q", "-b", "wt", str(wt_path))
    assert (wt_path / ".git").is_file()
    return wt_path


def seed_board(path, seed_ticket_id="T-001"):
    tickets = path / ".tickets"
    tickets.mkdir(parents=True, exist_ok=True)
    (tickets / (seed_ticket_id + ".json")).write_text(
        json.dumps({"id": seed_ticket_id, "status": "open"}))
    return tickets


def roles_written(board_dir):
    p = board_dir / "roles.json"
    return json.loads(p.read_text()) if p.is_file() else {}


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_join_from_linked_worktree_with_own_board_and_tickets_dir_main(tool, tmp_path):
    """C1 from cos-opus T-424 verdict: linked wt has its own .tickets, TICKETS_DIR
    names the main shared board -- join must succeed, not false-REFUSE."""
    main_repo = make_repo(tmp_path / "proj")
    main_board = seed_board(main_repo, seed_ticket_id="T-100")
    wt = make_linked_worktree(main_repo, tmp_path / "proj-wt")
    seed_board(wt, seed_ticket_id="T-001")

    r = run(tool, wt, "join", "linkedagent", "--roles", "backend",
            env=clean_env(tmp_path, TICKETS_DIR=str(main_board)))

    assert r.returncode == 0, "join must succeed, not false-REFUSE:\n%s" % (r.stdout + r.stderr)
    assert roles_written(main_board).get("linkedagent") == ["backend"]
    assert roles_written(wt / ".tickets") == {}
