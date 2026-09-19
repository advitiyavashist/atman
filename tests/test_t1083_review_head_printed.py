"""T-1083: accept is reachable from CLI output alone.

`atm accept` demands the full 40-char review head. review/show must print it,
and a short-sha accept refusal must include it so the user never has to read
JSON or run git rev-parse.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401
from test_t1031_accept_gate import TOOLS, TOOL_IDS, load_ticket, run

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import review_verdict  # noqa: E402

HEAD_RE = re.compile(r"review_head:\s*([0-9a-f]{40})")


def test_refuse_names_the_full_head():
    head = "c" * 40
    t = {"id": "T-001", "status": "review", "owner": "alice",
         "review_head": head}
    err = review_verdict.refuse(t, "reviewer", "c" * 7, "accept",
                                require_full=True, notes="ok")
    assert err and "full 40-character" in err
    assert head in err
    assert review_verdict.displayed_review_head(t) == head


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_accept_completes_from_review_and_show_output_only(tool, board):
    assert run(tool, board, "join", "alice", "--roles", "docs",
               agent="alice").returncode == 0
    assert run(tool, board, "join", "reviewer", "--roles", "backend",
               agent="reviewer").returncode == 0
    claim = run(tool, board, "claim", "T-001", agent="alice")
    assert claim.returncode == 0, claim.stderr + claim.stdout

    submitted = run(tool, board, "review", "T-001", "--notes", "paths, tests",
                    "--force", agent="alice")
    assert submitted.returncode == 0, submitted.stderr + submitted.stdout
    review_out = submitted.stdout + submitted.stderr
    match = HEAD_RE.search(review_out)
    assert match, "atm review must print review_head: <full 40>: %r" % review_out
    full = match.group(1)
    assert load_ticket(board, "T-001").get("review_head") == full

    shown = run(tool, board, "show", "T-001", agent="reviewer")
    assert shown.returncode == 0, shown.stderr + shown.stdout
    show_match = HEAD_RE.search(shown.stdout)
    assert show_match and show_match.group(1) == full, shown.stdout

    refused = run(tool, board, "accept", "T-001", "--sha", full[:7],
                  "--notes", "too short", agent="reviewer")
    assert refused.returncode != 0
    refusal = refused.stderr + refused.stdout
    assert "full 40-character" in refusal
    assert full in refusal

    # The sha comes from CLI output, not git or the ticket JSON.
    accepted = run(tool, board, "accept", "T-001", "--sha", full,
                   "--notes", "matches printed head", agent="reviewer")
    assert accepted.returncode == 0, accepted.stderr + accepted.stdout
    evs = load_ticket(board, "T-001").get("review_events") or []
    assert evs and evs[0]["sha"] == full
    assert evs[0]["by"] == "reviewer"
