"""T-012: a reopened review head must not be described as a done ticket."""
from __future__ import annotations

import pytest

from test_wakeup import board  # noqa: F401
from test_t1031_accept_gate import (
    TOOLS, TOOL_IDS, load_ticket, put_in_review, run,
)


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_reopened_claimed_predecessor_has_status_aware_gate(tool, board):
    for name in ("alice", "bob", "carol"):
        joined = run(tool, board, "join", name, "--roles", "backend", agent=name)
        assert joined.returncode == 0, joined.stderr + joined.stdout
    for title in ("Original work", "Dependent work"):
        created = run(tool, board, "create", title, "--role", "backend", agent="alice")
        assert created.returncode == 0, created.stderr + created.stdout

    head, _ = put_in_review(board, tid="T-002", owner="alice")
    reopened = run(tool, board, "reopen", "T-002", "--notes", "revise implementation",
                   agent="alice")
    assert reopened.returncode == 0, reopened.stderr + reopened.stdout
    claimed = run(tool, board, "claim", "T-002", agent="bob")
    assert claimed.returncode == 0, claimed.stderr + claimed.stdout
    parent = load_ticket(board, "T-002")
    assert parent["status"] == "claimed" and parent["review_head"] == head

    dep = run(tool, board, "dep", "T-003", "--after", "T-002", agent="alice")
    assert dep.returncode == 0, dep.stderr + dep.stdout
    child = load_ticket(board, "T-003")
    assert child["deps"] == ["T-002"]

    assigned = run(tool, board, "assign", "T-003", "--owner", "carol", agent="alice")
    assert assigned.returncode != 0
    out = assigned.stderr + assigned.stdout
    assert "T-002 is in progress" in out
    assert "marked done without verification" not in out
    assert load_ticket(board, "T-003")["status"] == "open"
