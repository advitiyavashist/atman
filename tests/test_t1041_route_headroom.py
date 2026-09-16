"""T-1041: skip LIMITED seats; prefer headroom then cheaper --cost; hold all-limited."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ticket_board import route_headroom as rh
from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]
RESET = "2026-09-20T11:00:00Z"


def run(tool, board, *args, agent="", env=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"),
             TICKETS_DISPATCH_NO_SPAWN="1",
             PYTHONPATH=str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""))
    e.pop("TICKETS_STOP_HOOK", None)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                          text=True, env=e, cwd=str(board.parent))


def show(tool, board, tid, agent="alice"):
    r = run(tool, board, "show", tid, "--json", agent=agent)
    assert r.returncode == 0, r.stderr + r.stdout
    return json.loads(r.stdout)


def _stamp_seen(board, name):
    path = board / "agents" / ("%s.json" % name)
    rec = json.loads(path.read_text()) if path.exists() else {"owner": name}
    rec["seen"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps(rec, indent=2))


def _limit(board, name, harness="claude", reset=RESET):
    path = board / "agents" / ("%s.json" % name)
    rec = json.loads(path.read_text()) if path.exists() else {"owner": name}
    rec["limit"] = {
        "at": "2026-09-16T02:00:00Z",
        "reset_at": reset,
        "until": "Sep 20",
        "note": "session limit",
        "harness": harness,
        "source": "provider",
    }
    rec["seen"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(rec, indent=2))


def _pair(tool, board):
    assert run(tool, board, "join", "alice", "--roles", "docs",
               "--cost", "high", "--harness", "claude", agent="alice").returncode == 0
    assert run(tool, board, "join", "bob", "--roles", "docs",
               "--cost", "low", "--harness", "codex", agent="bob").returncode == 0
    _stamp_seen(board, "alice")
    _stamp_seen(board, "bob")


def test_pick_seat_skips_limited_and_prefers_cheaper():
    limited, cheap, dear = (
        {"name": "lim", "limited": True, "remaining": 90, "cost": 0, "score": 99,
         "limit_label": "claude limited until 2026-09-20T11:00:00Z"},
        {"name": "cheap", "limited": False, "remaining": None, "cost": 0, "score": 10},
        {"name": "dear", "limited": False, "remaining": None, "cost": 2, "score": 10},
    )
    name, why = rh.pick_seat([limited, cheap, dear])
    assert name == "cheap"
    assert "skipped limited: lim" in why
    assert "claude limited until 2026-09-20T11:00:00Z" in why


def test_pick_seat_prefers_observed_headroom_then_cost():
    low_empty = {"name": "low", "limited": False, "remaining": None, "cost": 0, "score": 10}
    mid_room = {"name": "mid", "limited": False, "remaining": 40, "cost": 1, "score": 10}
    name, why = rh.pick_seat([low_empty, mid_room])
    assert name == "mid"
    assert "headroom 40%" in why


def test_pick_seat_all_limited_names_resets():
    rows = [
        {"name": "alice", "limited": True, "remaining": None, "cost": 0, "score": 10,
         "limit_label": "claude limited until 2026-09-20T11:00:00Z"},
        {"name": "bob", "limited": True, "remaining": None, "cost": 1, "score": 10,
         "limit_label": "codex limited until 2026-09-21T00:00:00Z"},
    ]
    name, why = rh.pick_seat(rows)
    assert name is None
    assert why.startswith("HOLD:")
    assert "alice (claude limited until 2026-09-20T11:00:00Z)" in why
    assert "bob (codex limited until 2026-09-21T00:00:00Z)" in why


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_route_never_selects_limited_and_records_reason(tool, board):
    _pair(tool, board)
    _limit(board, "alice", harness="claude")
    routed = run(tool, board, "route", "--only", "alice", "bob", agent="bob")
    assert routed.returncode == 0, routed.stderr + routed.stdout
    t = show(tool, board, "T-001", agent="bob")
    assert t.get("suggested") == "bob"
    assert not t.get("hold")
    notes = " ".join(n.get("text", "") for n in t.get("notes") or [])
    assert "route:" in notes
    assert "skipped limited: alice" in notes
    assert "claude limited until %s" % RESET in notes


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_route_tie_goes_to_cheaper_tier(tool, board):
    _pair(tool, board)
    routed = run(tool, board, "route", "--only", "alice", "bob", agent="bob")
    assert routed.returncode == 0, routed.stderr + routed.stdout
    t = show(tool, board, "T-001", agent="bob")
    assert t.get("suggested") == "bob"
    notes = " ".join(n.get("text", "") for n in t.get("notes") or [])
    assert "cost low" in notes


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_all_limited_holds_with_resets_and_no_retry(tool, board):
    _pair(tool, board)
    _limit(board, "alice", harness="claude", reset=RESET)
    _limit(board, "bob", harness="codex", reset="2026-09-21T00:00:00Z")
    first = run(tool, board, "route", "--only", "alice", "bob", agent="bob")
    assert first.returncode == 0, first.stderr + first.stdout
    t = show(tool, board, "T-001", agent="bob")
    assert t.get("hold") is True
    assert not t.get("suggested")
    reason = t.get("hold_reason") or ""
    assert "HOLD:" in reason
    assert RESET in reason
    assert "2026-09-21T00:00:00Z" in reason
    hold_notes = [n for n in t.get("notes") or [] if "HOLD:" in n.get("text", "")]
    assert len(hold_notes) == 1
    second = run(tool, board, "route", "--only", "alice", "bob", "--redo", agent="bob")
    assert second.returncode == 0, second.stderr + second.stdout
    t2 = show(tool, board, "T-001", agent="bob")
    hold_notes2 = [n for n in t2.get("notes") or [] if "HOLD:" in n.get("text", "")]
    assert len(hold_notes2) == 1
    assert t2.get("suggested") in (None, "")


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_next_refuses_limited_seat(tool, board):
    _pair(tool, board)
    _limit(board, "alice", harness="claude")
    denied = run(tool, board, "next", agent="alice")
    assert denied.returncode != 0
    blob = denied.stdout + denied.stderr
    assert "claude limited until %s" % RESET in blob
    assert "not dispatching" in blob
    t = show(tool, board, "T-001", agent="bob")
    assert t["status"] == "open"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_next_dispatch_holds_when_every_candidate_is_limited(tool, board):
    _pair(tool, board)
    _limit(board, "alice", harness="claude")
    _limit(board, "bob", harness="codex", reset="2026-09-21T00:00:00Z")
    r = run(tool, board, "next", "--dispatch", agent="bob")
    assert r.returncode == 0, r.stderr + r.stdout
    t = show(tool, board, "T-001", agent="bob")
    assert t.get("hold") is True
    assert RESET in (t.get("hold_reason") or "")
    assert t["status"] == "open"
    assert not t.get("reserved_for")


def test_dispatch_refuses_limited_target(board):
    tool = TOOLS[0]
    _pair(tool, board)
    _limit(board, "alice", harness="claude")
    r = run(tool, board, "dispatch", "T-001", "--to", "alice", "--harness", "cursor",
            agent="bob")
    assert r.returncode != 0
    blob = r.stdout + r.stderr
    assert "claude limited until %s" % RESET in blob
    t = show(tool, board, "T-001", agent="bob")
    assert not t.get("reserved_for")


def test_spawn_refuses_limited_seat(board):
    tool = TOOLS[0]
    _pair(tool, board)
    _limit(board, "alice", harness="claude")
    r = run(tool, board, "spawn", "alice", agent="alice")
    assert r.returncode != 0
    assert "claude limited until %s" % RESET in (r.stdout + r.stderr)
