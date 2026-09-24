"""T-1044: successor handoff carries the accept verdict, not the worker claim.

CEO first-user sequence: review with a false pytest claim, done without a
verdict (child BLOCKED), then accept. The child's Handoff from dependencies
and atm next must show the accept note, drop the superseded gate note, and
not present the contradicted REVIEW claim as the current verdict.
"""
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
from ticket_board import work_view  # noqa: E402

TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]
FALSE_CLAIM = "greet.py + tests/test_greet.py; pytest -q: 1 passed"
ACCEPT_NOTE = "ran unittest discover myself; note's pytest claim unverified (pytest absent)"
GATE = "T-002 marked done without verification; reopen it, then review and accept"
CLOSE_NOTE = "closing without a verdict"


def run(tool, board, *args, agent=""):
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


def load_ticket(board, tid):
    return json.loads((board / (tid + ".json")).read_text())


def save_ticket(board, t):
    (board / (t["id"] + ".json")).write_text(json.dumps(t, indent=2) + "\n")


def repo_shas(board):
    repo = board.parent
    full = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return full


def setup_chain(tool, board):
    for name in ("dev1", "dev2", "boss"):
        r = run(tool, board, "join", name, "--roles", "backend", agent=name)
        assert r.returncode == 0, r.stderr + r.stdout
    parent = run(tool, board, "create", "add greet", "--role", "backend", agent="dev1")
    assert parent.returncode == 0, parent.stderr
    child = run(tool, board, "create", "document greet", "--role", "backend",
                "--deps", "T-002", agent="dev1")
    assert child.returncode == 0, child.stderr
    return "T-002", "T-003"


def put_in_review(board, tid, owner="dev1"):
    t = load_ticket(board, tid)
    full = repo_shas(board)
    t["status"] = "review"
    t["owner"] = owner
    t["role"] = "backend"
    t["review_at"] = "2026-09-16T03:00:00Z"
    t["review_head"] = full
    t["commit"] = "dev1/work@%s" % full[:7]
    t["branch"] = "dev1/work"
    t.setdefault("notes", []).append({
        "by": owner, "at": "2026-09-16T03:00:00Z",
        "text": "REVIEW: %s -- %s" % (t["commit"], FALSE_CLAIM),
    })
    save_ticket(board, t)
    return full


def test_handoff_notes_pure():
    sha = "a" * 40
    parent = {
        "id": "T-001", "status": "done", "role": "backend",
        "review_at": "2026-09-16T03:00:00Z", "review_head": sha,
        "notes": [
            {"by": "dev1", "text": "REVIEW: br@abc -- " + FALSE_CLAIM},
            {"by": "boss", "text": CLOSE_NOTE},
            {"by": "boss", "text": GATE.replace("T-002", "T-001")},
        ],
        "review_events": [{
            "kind": "accept", "by": "boss", "at": "2026-09-16T03:10:00Z",
            "sha": sha, "notes": ACCEPT_NOTE,
        }],
    }
    inherited = work_view.handoff_notes(parent)
    texts = [n.get("text") or "" for n in inherited]
    assert ACCEPT_NOTE in texts
    assert not any(t.startswith("REVIEW:") for t in texts)
    assert not any(FALSE_CLAIM in t for t in texts)
    assert not any("marked done without verification" in t for t in texts)
    assert CLOSE_NOTE not in texts
    assert inherited[-1]["kind"] == "accept"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_accept_note_travels_in_successor_handoff_and_next(tool, board):
    parent_id, child_id = setup_chain(tool, board)
    full = put_in_review(board, parent_id)
    done = run(tool, board, "done", parent_id, "--notes", CLOSE_NOTE,
               "--force", agent="boss")
    assert done.returncode == 0, done.stderr + done.stdout
    assert load_ticket(board, child_id)["status"] == "blocked"
    acc = run(tool, board, "accept", parent_id, "--sha", full,
              "--notes", ACCEPT_NOTE, agent="boss")
    assert acc.returncode == 0, acc.stderr + acc.stdout
    assert "unblocked: %s" % child_id in acc.stdout

    shown = run(tool, board, "show", child_id, agent="dev2")
    assert shown.returncode == 0, shown.stderr + shown.stdout
    handoff = shown.stdout
    assert "Handoff from dependencies" in handoff
    assert ACCEPT_NOTE in handoff
    assert GATE not in handoff
    assert "marked done without verification" not in handoff
    assert CLOSE_NOTE not in handoff
    assert FALSE_CLAIM not in handoff
    assert "REVIEW:" not in handoff.split("Handoff from dependencies")[-1].split("Notes:")[0]

    nxt = run(tool, board, "next", agent="dev2")
    assert nxt.returncode == 0, nxt.stderr + nxt.stdout
    assert child_id in nxt.stdout
    assert ACCEPT_NOTE in nxt.stdout
    assert GATE not in nxt.stdout
    assert CLOSE_NOTE not in nxt.stdout
    assert FALSE_CLAIM not in nxt.stdout
