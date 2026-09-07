"""T-409: unbound cwd must not write a live board (zed / T-331 class).

Red-before-green: with TICKETS_DIR unset, `tickets join` from a parent folder
that has exactly one child live board used to write that child's agents/.
After the guard it refuses. An explicit TICKETS_DIR still binds. A cwd that
IS the checkout still works. Both tickets.py and cli.py copies are covered.
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
        "PYTEST_CURRENT_TEST": "tests/test_t409_unbound_board.py::simulated (call)",
    }
    for passthrough in ("TMPDIR", "TMP", "TEMP"):
        if os.environ.get(passthrough):
            e[passthrough] = os.environ[passthrough]
    e.pop("TICKETS_DIR", None)
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    e.update(overrides)
    return e


def run(tool, cwd, *args, env=None):
    return subprocess.run(
        [sys.executable, str(tool), *args],
        capture_output=True, text=True, cwd=str(cwd), env=env,
    )


def live_ticket(board):
    board.mkdir(parents=True, exist_ok=True)
    (board / "T-001.json").write_text(json.dumps({
        "id": "T-001", "title": "x", "status": "open",
        "created": "2026-01-01T00:00:00Z", "updated": "2026-01-01T00:00:00Z",
        "priority": 2, "deps": [], "blocks": [],
    }))


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_parent_cwd_without_tickets_dir_refuses_the_child_live_board(tmp_path, tool):
    """The Downloads -> unique-child-board vector. This is the zed class."""
    parent = tmp_path / "downloads"
    project = parent / "steer"
    stray = parent / "mktemp-sandbox"
    parent.mkdir()
    stray.mkdir()
    live_ticket(project / ".tickets")
    env = clean_env(tmp_path)
    before = set((project / ".tickets").glob("agents/*.json")) if (project / ".tickets" / "agents").exists() else set()
    r = run(tool, parent, "join", "zed", "--roles", "backend", env=env)
    assert r.returncode != 0, r.stdout + r.stderr
    err = r.stderr + r.stdout
    assert "REFUSING TO USE BOARD" in err
    assert "TICKETS_DIR is unset" in err
    agents = project / ".tickets" / "agents"
    after = set(agents.glob("*.json")) if agents.exists() else set()
    assert after == before
    assert not (agents / "zed.json").exists()


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_mktemp_cwd_does_not_write_a_sibling_live_board(tmp_path, tool):
    sibling = tmp_path / "project"
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    live_ticket(sibling / ".tickets")
    env = clean_env(tmp_path)
    run(tool, sandbox, "join", "zed", "--roles", "backend", env=env)
    # sandbox is not a parent of the live board; discovery must not find it.
    # A local board in sandbox is fine. The sibling live board must stay clean.
    assert not (sibling / ".tickets" / "agents" / "zed.json").exists()


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_explicit_tickets_dir_still_binds_from_an_unbound_cwd(tmp_path, tool):
    parent = tmp_path / "downloads"
    project = parent / "steer"
    parent.mkdir()
    live_ticket(project / ".tickets")
    env = clean_env(tmp_path, TICKETS_DIR=str(project / ".tickets"), TICKET_AGENT="zed")
    r = run(tool, parent, "join", "zed", "--roles", "backend", env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    assert (project / ".tickets" / "agents" / "zed.json").exists()


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_cwd_inside_the_checkout_still_resolves(tmp_path, tool):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    live_ticket(repo / ".tickets")
    env = clean_env(tmp_path)
    r = run(tool, repo, "board", env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "REFUSING TO USE BOARD" not in (r.stderr + r.stdout)
