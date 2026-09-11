"""T-781: HOLD is not claimable; done starts unblocked children."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]


def run(tool, board, *args, agent="", cwd=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    return subprocess.run(
        [sys.executable, str(tool), *args], capture_output=True, text=True,
        env=e, cwd=str(cwd or board.parent))


def created_id(r):
    m = re.search(r"created (T-\d+)", r.stdout)
    assert m, r.stdout + r.stderr
    return m.group(1)


def show(tool, board, tid, agent="alice"):
    r = run(tool, board, "show", tid, "--json", agent=agent)
    assert r.returncode == 0, r.stderr + r.stdout
    return json.loads(r.stdout)


def messages(board):
    p = board / "messages.jsonl"
    if not p.exists():
        return []
    out = []
    for ln in p.read_text().splitlines():
        if ln.strip():
            out.append(json.loads(ln))
    return out


def setup_docs(tool, board):
    assert run(tool, board, "join", "alice", "--roles", "docs", agent="alice").returncode == 0
    assert run(tool, board, "join", "bob", "--roles", "docs", agent="bob").returncode == 0
    assert run(tool, board, "join", "planner", "--roles", "backend", agent="planner").returncode == 0
    assert run(tool, board, "master", "take", "--owner", "planner", agent="planner").returncode == 0


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_next_skips_hold_body(tool, board):
    setup_docs(tool, board)
    r = run(tool, board, "create", "atm alias", "--role", "docs",
            "--body", "HOLD until tester week ends.")
    assert r.returncode == 0, r.stderr
    hold_id = created_id(r)
    # T-001 is also ready (Write docs). Claim it first so HOLD is the leftover.
    assert run(tool, board, "next", agent="alice").returncode == 0
    leftover = run(tool, board, "next", "--another", agent="alice")
    assert leftover.returncode != 0, leftover.stdout
    assert show(tool, board, hold_id)["status"] == "open"
    assert show(tool, board, hold_id).get("owner") in ("", None)


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_next_skips_hold_flag(tool, board):
    setup_docs(tool, board)
    r = run(tool, board, "create", "about flag", "--role", "docs")
    assert r.returncode == 0, r.stderr
    tid = created_id(r)
    held = run(tool, board, "hold", tid, "--reason", "tester week", agent="planner")
    assert held.returncode == 0, held.stderr
    assert show(tool, board, tid).get("hold") is True
    assert run(tool, board, "next", agent="alice").returncode == 0  # T-001
    leftover = run(tool, board, "next", "--another", agent="alice")
    assert leftover.returncode != 0, leftover.stdout
    assert show(tool, board, tid)["status"] == "open"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_done_starts_reserved_child(tool, board):
    setup_docs(tool, board)
    child = run(tool, board, "create", "child work", "--role", "docs", "--deps", "T-001")
    assert child.returncode == 0, child.stderr
    grandchild = run(tool, board, "create", "later work", "--role", "docs", "--deps", "T-002")
    assert grandchild.returncode == 0, grandchild.stderr
    assert run(tool, board, "reserve", "T-002", "--for", "bob", agent="planner").returncode == 0
    assert run(tool, board, "next", agent="alice").returncode == 0
    done = run(tool, board, "done", "T-001", "--notes", "parent closed", "--force",
               agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    assert "unblocked: T-002" in done.stdout
    assert "started: T-002 -> bob" in done.stdout
    assert "T-003" not in done.stdout
    assert show(tool, board, "T-002")["status"] == "open"
    assert show(tool, board, "T-003")["status"] == "open"
    msgs = [m for m in messages(board) if m.get("to") == "bob" and m.get("re") == "T-002"]
    assert msgs, messages(board)
    assert msgs[-1].get("kind") == "task"
    assert "success trigger" in msgs[-1]["text"]


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_done_does_not_start_held_child(tool, board):
    setup_docs(tool, board)
    assert run(tool, board, "create", "parked child", "--role", "docs",
               "--deps", "T-001", "--body", "HOLD parked").returncode == 0
    assert run(tool, board, "next", agent="alice").returncode == 0
    done = run(tool, board, "done", "T-001", "--notes", "parent closed", "--force",
               agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    assert "unblocked: T-002" in done.stdout
    assert "held (not started): T-002" in done.stdout
    assert "started:" not in done.stdout
    assert not [m for m in messages(board) if "success trigger" in m.get("text", "")]
