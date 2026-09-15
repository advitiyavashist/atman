"""T-1031: dependents release only after ACCEPT (or a recorded override).

Throwaway boards. Two-copy: tickets.py and src/ticket_board/cli.py.
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
REASON = "T-002 marked done without verification; accept it or reopen"


def run(tool, board, *args, agent=""):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
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


def messages(board):
    p = board / "messages.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def repo_shas(board):
    repo = board.parent
    full = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    short = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, text=True).strip()
    return full, short


def setup_backend(tool, board):
    for name in ("alice", "bob", "reviewer"):
        r = run(tool, board, "join", name, "--roles", "backend", agent=name)
        assert r.returncode == 0, r.stderr + r.stdout
    parent = run(tool, board, "create", "impl work", "--role", "backend", agent="alice")
    assert parent.returncode == 0, parent.stderr
    child = run(tool, board, "create", "follow on", "--role", "backend",
                "--deps", "T-002", agent="alice")
    assert child.returncode == 0, child.stderr


def put_in_review(board, tid="T-002", owner="alice"):
    t = load_ticket(board, tid)
    full, short = repo_shas(board)
    t["status"] = "review"
    t["owner"] = owner
    t["role"] = "backend"
    t["review_at"] = "2026-09-15T12:00:00Z"
    t["review_head"] = full
    t["commit"] = "fix/t1031@%s" % short
    t["branch"] = "fix/t1031"
    t.setdefault("notes", []).append({
        "by": owner, "at": "2026-09-15T12:00:00Z",
        "text": "REVIEW: %s -- paths" % t["commit"],
    })
    save_ticket(board, t)
    return full, short


def test_pure_gate_helpers():
    reviewed = {"id": "T-001", "status": "done", "role": "backend",
                "review_at": "2026-09-15T00:00:00Z", "review_head": "a" * 40,
                "commit": "br@" + "a" * 7}
    accepted = dict(reviewed, review_events=[{
        "kind": "accept", "by": "rev", "at": "2026-09-15T00:01:00Z",
        "sha": "a" * 40, "notes": "ok"}])
    docs = {"id": "T-009", "status": "done", "role": "docs"}
    assert work_view.went_through_review(reviewed) is True
    assert work_view.is_docs_exempt(reviewed) is False
    assert work_view.dep_released(reviewed) is False
    assert work_view.dep_released(accepted) is True
    assert work_view.accepted_release_sha(accepted) == "a" * 40
    assert work_view.is_docs_exempt(docs) is True
    assert work_view.dep_released(docs) is False
    docs["release_override"] = work_view.make_release_override(
        "docs-exempt", "alice", "2026-09-15T00:02:00Z", "docs-exempt")
    assert work_view.dep_released(docs) is True
    assert work_view.unverified_block_reason("T-002") == REASON
    assert work_view.accepted_release_note("T-002", "a" * 40, "ceo") == (
        "T-002 accepted at %s by ceo -- unblocked" % ("a" * 40))
    leftover = {"notes": [{"by": "alice", "text": REASON},
                          {"by": "alice", "text": "other"}]}
    work_view.drop_unverified_gate_notes(leftover, "T-002")
    assert [n["text"] for n in leftover["notes"]] == ["other"]
    assert work_view.is_live_unverified_gate_note(REASON, "T-002") is True
    assert work_view.is_live_unverified_gate_note("resolved: " + REASON, "T-002") is False


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_done_without_accept_keeps_successor_blocked(tool, board):
    setup_backend(tool, board)
    put_in_review(board)
    done = run(tool, board, "done", "T-002", "--notes", "paths", "--force",
               agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    assert "unblocked" not in done.stdout
    assert "started:" not in done.stdout
    assert "blocked: T-003 -- %s" % REASON in done.stdout
    child = load_ticket(board, "T-003")
    assert child["status"] == "blocked"
    assert child.get("unverified_block") == "T-002"
    assert any(REASON in (n.get("text") or "") for n in child.get("notes") or [])
    nxt = run(tool, board, "next", agent="bob")
    assert nxt.returncode != 0, nxt.stdout + nxt.stderr
    assert "T-003" not in nxt.stdout
    claim = run(tool, board, "claim", "T-003", agent="bob")
    assert claim.returncode != 0
    assert load_ticket(board, "T-003")["status"] == "blocked"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_accept_then_done_releases_handoff_sha(tool, board):
    setup_backend(tool, board)
    full, _ = put_in_review(board)
    acc = run(tool, board, "accept", "T-002", "--sha", full,
              "--notes", "verified artifact", agent="reviewer")
    assert acc.returncode == 0, acc.stderr + acc.stdout
    done = run(tool, board, "done", "T-002", "--notes", "handoff", "--force",
               agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    assert "unblocked: T-003" in done.stdout
    assert REASON not in done.stdout
    child = load_ticket(board, "T-003")
    assert child["status"] == "open"
    handoff = " ".join(n.get("text") or "" for n in child.get("notes") or [])
    assert full in handoff
    assert "success trigger" in handoff
    assert "accepted %s" % full in handoff
    assert REASON not in handoff
    shown = run(tool, board, "show", "T-003", agent="bob")
    assert shown.returncode == 0, shown.stderr + shown.stdout
    assert full in shown.stdout
    assert REASON not in shown.stdout


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_operator_override_is_recorded(tool, board):
    setup_backend(tool, board)
    put_in_review(board)
    done = run(tool, board, "done", "T-002", "--notes", "emergency",
               "--force", "--release-unverified", agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    assert "unblocked: T-003" in done.stdout
    assert "released (operator override)" in done.stdout
    parent = load_ticket(board, "T-002")
    ov = parent.get("release_override") or {}
    assert ov.get("kind") == "operator"
    assert ov.get("by") == "alice"
    assert "release-unverified" in (ov.get("reason") or "")
    child = load_ticket(board, "T-003")
    assert child["status"] == "open"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_docs_exempt_override_is_recorded(tool, board):
    assert run(tool, board, "join", "alice", "--roles", "docs",
               agent="alice").returncode == 0
    child = run(tool, board, "create", "next doc", "--role", "docs",
                "--deps", "T-001", agent="alice")
    assert child.returncode == 0, child.stderr
    done = run(tool, board, "done", "T-001", "--notes", "docs closed",
               "--force", agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    assert "unblocked: T-002" in done.stdout
    assert "released (docs-exempt override)" in done.stdout
    parent = load_ticket(board, "T-001")
    ov = parent.get("release_override") or {}
    assert ov.get("kind") == "docs-exempt"
    assert ov.get("by") == "alice"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_done_then_accept_releases_successor(tool, board):
    setup_backend(tool, board)
    full, _ = put_in_review(board)
    done = run(tool, board, "done", "T-002", "--notes", "closed early",
               "--force", agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    assert load_ticket(board, "T-003")["status"] == "blocked"
    acc = run(tool, board, "accept", "T-002", "--sha", full,
              "--notes", "verified after close", agent="reviewer")
    assert acc.returncode == 0, acc.stderr + acc.stdout
    assert "unblocked: T-003" in acc.stdout
    child = load_ticket(board, "T-003")
    assert child["status"] == "open"
    assert "unverified_block" not in child
    notes = [n.get("text") or "" for n in child.get("notes") or []]
    handoff = " ".join(notes)
    release = "T-002 accepted at %s by reviewer -- unblocked" % full
    assert full in handoff
    assert release in notes
    assert "accepted %s" % full in handoff
    assert REASON not in handoff
    assert not any(work_view.is_live_unverified_gate_note(t, "T-002") for t in notes)
    shown = run(tool, board, "show", "T-003", agent="bob")
    assert shown.returncode == 0, shown.stderr + shown.stdout
    assert full in shown.stdout
    assert release in shown.stdout
    assert REASON not in shown.stdout
