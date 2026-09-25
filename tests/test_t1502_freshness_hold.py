"""T-1502: freshness hold on claim/accept/reject when --seen is stale."""
from __future__ import annotations

import importlib.util
import json

from test_wakeup import TOOL, board, run  # noqa: F401


def _tc():
    path = TOOL.parent / "src" / "ticket_board" / "ticket_coordination.py"
    spec = importlib.util.spec_from_file_location("tc_t1502", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_revision_changes_on_status_and_head():
    tc = _tc()
    t = {
        "id": "T-1", "status": "review", "owner": "alice",
        "owner_generation": 1, "review_head": {"sha": "a" * 40},
        "notes": [], "updated": "2026-09-25T00:00:00Z",
    }
    r1 = tc.ticket_revision(t)
    t2 = dict(t)
    t2["review_head"] = {"sha": "b" * 40}
    r2 = tc.ticket_revision(t2)
    assert r1 != r2
    err = tc.freshness_hold_error(t2, r1)
    assert err and "moved since you last read" in err
    assert "b" * 40 in err or "review_head" in err
    assert tc.freshness_hold_error(t, r1) is None


def test_claim_race_loser_gets_freshness_reason(board):
    run(board, "create", "race me", "--role", "backend", agent="master")
    # find ticket id
    show = run(board, "list", "--json", agent="master")
    tickets = json.loads(show.stdout)
    tid = tickets[0]["id"]
    detail = run(board, "show", tid, agent="alice")
    assert "revision:" in detail.stdout
    rev = [ln.split()[1] for ln in detail.stdout.splitlines() if ln.startswith("revision:")][0]
    # Alice claims with seen
    r1 = run(board, "claim", tid, "--seen", rev, agent="alice")
    assert r1.returncode == 0, r1.stderr
    # Bob still has the old revision → freshness hold (or already taken)
    r2 = run(board, "claim", tid, "--seen", rev, agent="bob")
    assert r2.returncode != 0
    err = r2.stdout + r2.stderr
    assert ("moved since you last read" in err) or ("already taken" in err)


def test_accept_refuses_stale_seen_after_new_head(board):
    tc = _tc()
    run(board, "join", "alice", "--roles", "backend")
    run(board, "create", "pin me", "--role", "backend", agent="master")
    tickets = json.loads(run(board, "list", "--json", agent="master").stdout)
    tid = tickets[0]["id"]
    run(board, "claim", tid, agent="alice")
    # Fabricate an IN REVIEW ticket with a head.
    path = board / ("%s.json" % tid)
    t = json.loads(path.read_text())
    t["status"] = "review"
    t["review_head"] = {"sha": "a" * 40, "sha_full": "a" * 40}
    t["commit"] = "branch@" + ("a" * 7)
    t["updated"] = "2026-09-25T01:00:00Z"
    path.write_text(json.dumps(t, indent=2))
    seen = tc.ticket_revision(t)
    # New head lands.
    t["review_head"] = {"sha": "c" * 40, "sha_full": "c" * 40}
    t["updated"] = "2026-09-25T02:00:00Z"
    path.write_text(json.dumps(t, indent=2))
    r = run(board, "accept", tid, "--sha", "c" * 40, "--notes", "ok",
            "--seen", seen, agent="bob")
    assert r.returncode != 0
    assert "moved since you last read" in (r.stdout + r.stderr)
    assert ("c" * 40)[:12] in (r.stdout + r.stderr) or "review_head" in (r.stdout + r.stderr)
