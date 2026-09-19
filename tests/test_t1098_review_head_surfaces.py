"""T-1098: reviewer-facing surfaces print the full 40-char review head.

`atm accept` needs the exact SHA. After T-1083, show/review/refusal already
name it. The master REVIEW QUEUE and the seat brief still truncated.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401
from test_t1031_accept_gate import TOOLS, TOOL_IDS, load_ticket, run

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import review_verdict, seat_brief  # noqa: E402


def test_review_queue_pin_uses_full_head():
    head = "a" * 40
    t = {
        "id": "T-001",
        "status": "review",
        "branch": "work/t1",
        "commit": "work/t1@9809ac6",
        "review_head": head,
    }
    assert review_verdict.review_queue_pin(t) == "work/t1@" + head
    assert review_verdict.displayed_review_head(t) == head
    bare = review_verdict.review_queue_pin({
        "id": "T-009", "commit": "work/t1@9809ac6"})
    assert bare == "work/t1@9809ac6"


def test_seat_brief_prints_full_review_head():
    head = "b" * 40
    text = seat_brief.compose(
        "alice", ticket_id="T-001", ticket_title="impl",
        ticket_status="IN REVIEW", review_head=head)
    assert "in review at %s" % head in text
    assert "in review at %s" % head[:12] not in text.replace(head, "")


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_master_review_queue_prints_full_head(tool, board):
    assert run(tool, board, "join", "alice", "--roles", "docs",
               agent="alice").returncode == 0
    claim = run(tool, board, "claim", "T-001", agent="alice")
    assert claim.returncode == 0, claim.stderr + claim.stdout
    submitted = run(tool, board, "review", "T-001", "--notes", "paths",
                    "--force", agent="alice")
    assert submitted.returncode == 0, submitted.stderr + submitted.stdout
    full = load_ticket(board, "T-001").get("review_head") or ""
    assert len(full) == 40, full

    master = run(tool, board, "master", agent="boss")
    assert master.returncode == 0, master.stderr + master.stdout
    out = master.stdout
    assert "REVIEW QUEUE" in out
    assert full in out
    pin = load_ticket(board, "T-001").get("commit") or ""
    short = pin.rsplit("@", 1)[-1] if "@" in pin else ""
    if short and short != full:
        queue_lines = [ln for ln in out.splitlines() if ln.strip().startswith("T-001")]
        assert queue_lines, out
        assert full in queue_lines[0]
        assert ("@" + short) not in queue_lines[0] or full in queue_lines[0]


def test_tickets_py_seat_brief_names_full_head(board):
    tool = TOOLS[0]
    assert run(tool, board, "join", "alice", "--roles", "docs",
               agent="alice").returncode == 0
    assert run(tool, board, "claim", "T-001", agent="alice").returncode == 0
    submitted = run(tool, board, "review", "T-001", "--notes", "paths",
                    "--force", agent="alice")
    assert submitted.returncode == 0, submitted.stderr + submitted.stdout
    full = load_ticket(board, "T-001").get("review_head") or ""
    assert len(full) == 40, full

    spec = importlib.util.spec_from_file_location("tickets_t1098", ROOT / "tickets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = mod.seat_brief_text(str(board), "alice")
    assert "in review at %s" % full in text
