"""T-424: `tickets join` must not bind a probe seat to a board it did not
mean to join.

The defect: board_dir() honours $TICKETS_DIR unconditionally, and TICKETS_DIR
is exported in every agent's environment. An agent that mkdir's its own
throwaway .tickets/ in a temp dir to probe the tool and then runs `join` from
there registers a real seat on whatever board TICKETS_DIR names instead --
nothing in join's output says so. That is how opus-authz's probe seat 'zed'
landed on the live production board (two agents hit this inside half an
hour). `init` already refuses the equivalent shape (T-263); this file pins
the same refusal for `join`, which had no guard at all before this fix.

Same subprocess-CLI, two-copy pattern as test_t263_init_isolation.py: both
tickets.py and src/ticket_board/cli.py are exercised, because a fix that
reaches only one silently leaves the other broken (T-243).
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
        "PYTEST_CURRENT_TEST": "tests/test_t424_join_isolation.py::simulated (call)",
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


def make_board(path, seed_ticket_id="T-001"):
    """A directory with its OWN .tickets/, seeded so it's distinguishable from
    an empty one -- no git repo required, join doesn't need one."""
    path.mkdir(parents=True, exist_ok=True)
    tickets = path / ".tickets"
    tickets.mkdir()
    (tickets / (seed_ticket_id + ".json")).write_text(
        json.dumps({"id": seed_ticket_id, "status": "open"}))
    return path


def roles_written(board_dir):
    p = board_dir / ".tickets" / "roles.json"
    return json.loads(p.read_text()) if p.is_file() else {}


# --------------------------------------------------------------------------
# The exact reported shape: TICKETS_DIR points at board A (production, here
# standing in for the live board), cwd has its own .tickets for board B (the
# probe). Before the fix, join silently registers the seat on A.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_join_refuses_when_tickets_dir_shadows_cwds_own_board(tool, tmp_path):
    board_a = make_board(tmp_path / "production", seed_ticket_id="T-100")
    board_b = make_board(tmp_path / "probe", seed_ticket_id="T-001")

    r = run(tool, board_b, "join", "zed", "--roles", "backend",
            env=clean_env(tmp_path, TICKETS_DIR=str(board_a / ".tickets")))

    assert r.returncode != 0, "join must FAIL, not silently bind the wrong board:\n%s" % (r.stdout + r.stderr)
    out = r.stdout + r.stderr
    assert "REFUSING TO JOIN" in out
    # Both resolved paths must be named -- an agent must see which board it
    # actually hit without reading the source (acceptance criterion 2).
    assert str(board_b / ".tickets") in out
    assert str(board_a / ".tickets") in out
    # Nothing was written to either board.
    assert roles_written(board_a) == {}
    assert not (board_a / ".tickets" / "agents").exists()
    assert roles_written(board_b) == {}


# --------------------------------------------------------------------------
# The ordinary case must keep working: no .tickets sitting in cwd at all, so
# there is nothing for TICKETS_DIR to shadow.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_join_with_no_cwd_board_uses_tickets_dir_normally(tool, tmp_path):
    board_a = make_board(tmp_path / "production", seed_ticket_id="T-100")
    empty_cwd = tmp_path / "not-a-board"
    empty_cwd.mkdir()

    r = run(tool, empty_cwd, "join", "newagent", "--roles", "backend",
            env=clean_env(tmp_path, TICKETS_DIR=str(board_a / ".tickets")))

    assert r.returncode == 0, r.stdout + r.stderr
    assert roles_written(board_a).get("newagent") == ["backend"]


# --------------------------------------------------------------------------
# TICKETS_DIR pointing at the SAME board cwd's own .tickets resolves to is
# not a mismatch -- must not refuse.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_join_when_tickets_dir_matches_cwd_board_is_not_a_mismatch(tool, tmp_path):
    board = make_board(tmp_path / "same", seed_ticket_id="T-001")

    r = run(tool, board, "join", "agentx", "--roles", "backend",
            env=clean_env(tmp_path, TICKETS_DIR=str(board / ".tickets")))

    assert r.returncode == 0, r.stdout + r.stderr
    assert roles_written(board).get("agentx") == ["backend"]
