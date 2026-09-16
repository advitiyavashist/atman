"""T-1051: one timeline per run — both seats, accept evidence, stall, missing."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import trace as tr  # noqa: E402

TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]
SHA_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SHA_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def run(tool, board, *args, agent="boss"):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"),
             TICKETS_GC_OPEN_PRS="none")
    e.pop("TICKETS_STOP_HOOK", None)
    e.pop("TICKET_SEAT", None)
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID",
                "CURSOR_SESSION_ID", "TERM_SESSION_ID", "TICKET_SESSION_ID"):
        e.pop(var, None)
    if agent:
        e["TICKET_SESSION_ID"] = "test-session-" + agent
    e["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + e.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(tool), *args], capture_output=True, text=True,
        env=e, cwd=str(board.parent))


def save(board, rec):
    (board / (rec["id"] + ".json")).write_text(json.dumps(rec, indent=2) + "\n")


def write_objective(board):
    rec = {
        "text": "ship greet A then B",
        "set_by": "boss",
        "at": "2026-09-16T10:00:00Z",
        "done": True,
        "state": "achieved",
        "done_at": "2026-09-16T11:00:00Z",
        "exit_criterion": "both tickets accepted",
        "evidence": "T-001 and T-002 accepted",
    }
    (board / "objective.json").write_text(json.dumps(rec, indent=2) + "\n")
    return rec


def write_workforce(board):
    rec = {
        "alice": {"tool": "claude", "model": "sonnet", "roles": ["backend"]},
        "bob": {"tool": "codex", "model": "gpt-5", "roles": ["backend"]},
        "reviewer": {"tool": "claude", "model": "opus", "roles": ["backend"]},
    }
    (board / "workforce.json").write_text(json.dumps(rec, indent=2) + "\n")


def write_ab_board(board, stalled=False):
    write_objective(board)
    write_workforce(board)
    # board fixture already created T-001 as docs; overwrite as A, add B.
    save(board, {
        "id": "T-001",
        "title": "implement greet",
        "status": "done",
        "role": "backend",
        "owner": "alice",
        "created": "2026-09-16T10:01:00Z",
        "claimed_at": "2026-09-16T10:10:00Z",
        "review_at": "2026-09-16T10:20:00Z",
        "done_at": "2026-09-16T10:30:00Z",
        "review_head": SHA_A,
        "commit": "fix/a@" + SHA_A[:7],
        "notes": [
            {"by": "alice", "at": "2026-09-16T10:20:00Z",
             "text": "REVIEW: fix/a@%s -- greet.py + tests/test_greet.py; pytest -q: 1 passed" % SHA_A[:7]},
            {"by": "alice", "at": "2026-09-16T10:18:00Z",
             "text": "pytest -q: 1 passed"},
        ],
        "review_events": [{
            "kind": "accept", "by": "reviewer", "at": "2026-09-16T10:30:00Z",
            "sha": SHA_A, "notes": "ran unittest discover myself; pytest claim unverified",
        }],
    })
    save(board, {
        "id": "T-002",
        "title": "follow-on docs",
        "status": "done",
        "role": "backend",
        "owner": "bob",
        "deps": ["T-001"],
        "created": "2026-09-16T10:02:00Z",
        "claimed_at": "2026-09-16T10:40:00Z",
        "review_at": "2026-09-16T10:50:00Z",
        "done_at": "2026-09-16T10:55:00Z",
        "review_head": SHA_B,
        "commit": "fix/b@" + SHA_B[:7],
        "notes": [
            {"by": "bob", "at": "2026-09-16T10:50:00Z",
             "text": "REVIEW: fix/b@%s -- docs" % SHA_B[:7]},
        ],
        "review_events": [{
            "kind": "accept", "by": "reviewer", "at": "2026-09-16T10:55:00Z",
            "sha": SHA_B, "notes": "docs match accepted greet SHA",
        }],
        "steers": [{
            "id": "ste-test", "kind": "redirect", "from": "boss", "to": "bob",
            "ticket": "T-002", "at": "2026-09-16T10:45:00Z",
            "text": "keep the accept note in the follow-up",
            "receipt": "delivered-confirmed", "delivered": True,
        }],
    })
    agents = board / "agents"
    agents.mkdir(exist_ok=True)
    alice = {
        "owner": "alice",
        "ticket": "T-001",
        "cwd": str(board.parent / "no-transcript-tree"),
        "harness": "claude",
        "updated": "2026-09-16T10:25:00Z",
    }
    if stalled:
        alice["stall"] = {
            "at": "2026-09-16T10:25:00Z",
            "measured_s": 640,
            "last_output_at": "2026-09-16T10:14:20Z",
            "source": "watch",
        }
    (agents / "alice.json").write_text(json.dumps(alice) + "\n")
    (agents / "bob.json").write_text(json.dumps({
        "owner": "bob",
        "ticket": "T-002",
        "cwd": str(board.parent / "also-no-transcript"),
        "harness": "codex",
        "updated": "2026-09-16T10:52:00Z",
    }) + "\n")
    (board / "messages.jsonl").write_text(json.dumps({
        "at": "2026-09-16T10:35:00Z", "from": "boss", "to": "bob",
        "re": "T-002", "text": "T-001 accepted; start from that SHA",
    }) + "\n")
    return SHA_A, SHA_B


def test_collect_ab_objective_has_both_seats_verdicts_shas(board):
    write_ab_board(board)
    events, err = tr.collect(str(board), home=str(board.parent.parent / "home"))
    assert err == ""
    assert "alice" in {e["seat"] for e in events}
    assert "bob" in {e["seat"] for e in events}
    accepts = [e for e in events if e["kind"] == "accept" and not e.get("superseded")]
    assert [e["ticket"] for e in accepts] == ["T-001", "T-002"]
    assert [e["sha"] for e in accepts] == [SHA_A, SHA_B]
    assert accepts[0]["at"] < accepts[1]["at"]
    handoff = [e for e in events if e["kind"] == "handoff"]
    assert handoff and handoff[0]["ticket"] == "T-002"
    assert handoff[0]["from_ticket"] == "T-001"
    assert handoff[0]["sha"] == SHA_A
    assert "unittest" in (handoff[0].get("notes") or "")
    assert any(e["kind"] == "steer" and e["ticket"] == "T-002" for e in events)
    assert any(e["kind"] == "test" for e in events)


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_cli_ab_timeline_plain_and_json(tool, board):
    write_ab_board(board)
    plain = run(tool, board, "trace")
    assert plain.returncode == 0, plain.stderr + plain.stdout
    out = plain.stdout
    assert "alice" in out and "bob" in out
    assert "T-001" in out and "T-002" in out
    assert "  accept  " in out
    assert SHA_A in out and SHA_B in out
    assert out.index(SHA_A) < out.index(SHA_B)
    assert "handoff" in out and "from_ticket=T-001" in out
    assert "ran unittest discover myself" in out
    by_ticket = run(tool, board, "trace", "T-002")
    assert by_ticket.returncode == 0, by_ticket.stderr
    assert SHA_A in by_ticket.stdout and SHA_B in by_ticket.stdout
    by_obj = run(tool, board, "trace", "ship greet A then B")
    assert by_obj.returncode == 0, by_obj.stderr
    js = run(tool, board, "trace", "--json")
    assert js.returncode == 0, js.stderr
    events = json.loads(js.stdout)
    assert any(e["kind"] == "accept" and e["sha"] == SHA_A for e in events)
    assert any(e["kind"] == "accept" and e["sha"] == SHA_B for e in events)


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_stalled_seat_shows_measured_stall(tool, board):
    write_ab_board(board, stalled=True)
    r = run(tool, board, "trace", "T-001")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "  stall  " in r.stdout
    assert "measured_s=640" in r.stdout
    assert "2026-09-16T10:14:20Z" in r.stdout
    assert "threshold" not in r.stdout.lower()


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_missing_transcript_is_labelled_missing(tool, board):
    write_ab_board(board)
    r = run(tool, board, "trace", "T-001")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "  transcript  " in r.stdout
    assert "status=missing" in r.stdout
    assert "status=present" not in r.stdout
    # honesty: never an empty success for a missing session
    for line in r.stdout.splitlines():
        if "  transcript  " in line:
            assert "missing" in line
            assert " success" not in line.lower()


def test_unknown_ticket_exits(board):
    write_ab_board(board)
    r = run(TOOLS[0], board, "trace", "T-999")
    assert r.returncode != 0
    assert "T-999" in (r.stderr + r.stdout)
