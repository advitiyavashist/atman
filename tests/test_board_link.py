"""`atm board-link` and `atm where` (board location lookup).

Agents were copying one operator's absolute board path out of docs. The fix:
`atm where` says how it found the board (stderr; stdout line 1 stays the
path), and `atm board-link <board>` records "this repo uses that board" in
the machine config (the T-959 map), so every checkout and linked worktree of
the repo resolves the shared board with no TICKETS_DIR and no hardcoded path.

Both CLI copies are exercised (T-243).
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


def env_for(tmp_path, **extra):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    e = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "ATMAN_BOARD_CONFIG": str(tmp_path / "board.json"),
        "PYTEST_CURRENT_TEST": "tests/test_board_link.py::simulated (call)",
    }
    for passthrough in ("TMPDIR", "TMP", "TEMP"):
        if os.environ.get(passthrough):
            e[passthrough] = os.environ[passthrough]
    e.update(extra)
    return e


def run(tool, cwd, *args, env):
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                          text=True, cwd=str(cwd), env=env)


def git_repo(path):
    path.mkdir(parents=True)
    for cmd in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"]):
        subprocess.run(["git", *cmd], cwd=str(path), check=True)
    return path


@pytest.fixture
def world(tmp_path):
    shared = git_repo(tmp_path / "shared")
    board = shared / ".tickets"
    env = env_for(tmp_path)
    r = run(TOOLS[0], shared, "create", "seed", env={**env, "TICKETS_DIR": str(board)})
    assert r.returncode == 0, r.stderr
    app = git_repo(tmp_path / "app")
    (app / "f").write_text("x")
    subprocess.run(["git", "add", "f"], cwd=str(app), check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=str(app), check=True)
    return tmp_path, board.resolve(), app, env


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_where_explains_an_unlinked_repo(world, tool):
    tmp, board, app, env = world
    r = run(tool, app, "where", env=env)
    assert r.returncode == 0, r.stderr
    assert Path(r.stdout.splitlines()[0]).resolve() == (app / ".tickets").resolve()
    assert "no board is linked" in r.stderr
    assert "atm board-link" in r.stderr


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_link_makes_repo_and_its_worktrees_resolve_the_shared_board(world, tool):
    tmp, board, app, env = world
    r = run(tool, app, "board-link", str(board), env=env)
    assert r.returncode == 0, r.stderr
    cfg = json.loads((tmp / "board.json").read_text())
    assert cfg["boards"] == {str(app.resolve()): str(board)}

    r = run(tool, app, "where", env=env)
    assert Path(r.stdout.splitlines()[0]).resolve() == board
    assert "linked to repo" in r.stderr

    wt = tmp / "app-wt"
    subprocess.run(["git", "worktree", "add", "-q", str(wt)], cwd=str(app), check=True)
    r = run(tool, wt, "where", env=env)
    assert r.returncode == 0, r.stderr
    assert Path(r.stdout.splitlines()[0]).resolve() == board


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_link_refuses_a_directory_that_is_not_a_board(world, tool):
    tmp, board, app, env = world
    empty = tmp / "empty"
    empty.mkdir()
    r = run(tool, app, "board-link", str(empty), env=env)
    assert r.returncode != 0
    assert "does not look like a board" in (r.stdout + r.stderr)
    assert not (tmp / "board.json").exists()


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_show_and_unlink(world, tool):
    tmp, board, app, env = world
    assert run(tool, app, "board-link", str(board), env=env).returncode == 0
    r = run(tool, app, "board-link", "--show", env=env)
    assert "%s -> %s" % (app.resolve(), board) in r.stdout
    r = run(tool, app, "board-link", "--unlink", env=env)
    assert r.returncode == 0, r.stderr
    assert json.loads((tmp / "board.json").read_text())["boards"] == {}
    r = run(tool, app, "board-link", "--unlink", env=env)
    assert r.returncode != 0


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_unreadable_config_is_never_overwritten(world, tool):
    tmp, board, app, env = world
    (tmp / "board.json").write_text("{not json")
    r = run(tool, app, "board-link", str(board), env=env)
    assert r.returncode != 0
    assert "nothing changed" in (r.stdout + r.stderr)
    assert (tmp / "board.json").read_text() == "{not json"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_link_keeps_other_repos_and_warns_when_a_local_primary_still_wins(world, tool):
    tmp, board, app, env = world
    (tmp / "board.json").write_text(json.dumps({"boards": {"/elsewhere": "/elsewhere/.tickets"}, "x": 1}))
    local = app / ".tickets"
    local.mkdir()
    (local / ".primary").write_text("marked primary\n")
    r = run(tool, app, "board-link", str(board), env=env)
    assert r.returncode == 0, r.stderr
    assert "marked primary and still wins" in r.stdout
    cfg = json.loads((tmp / "board.json").read_text())
    assert cfg["x"] == 1
    assert cfg["boards"]["/elsewhere"] == "/elsewhere/.tickets"
    assert cfg["boards"][str(app.resolve())] == str(board)
