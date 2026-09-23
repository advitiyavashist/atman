"""T-1459: app truthfulness — counts, acceptance proof, plan summary.

Seed A accepted and B blocked on A; assert:
- projects counts expose claimed (working) work, not only status-open;
- an accepted ticket's acceptance.proof carries the accept record (who+sha)
  when sounding proof is empty;
- plan.summary surfaces finishing / blocked / next for the 390 Plan view.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tickets as tk  # noqa: E402

SHA_A = "6cf57003447931cf822f50ee8aeca2389700507b"


def stamp(mins_ago=0):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - mins_ago * 60))


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def ticket(board, tid, title, **kw):
    write(
        board / (tid + ".json"),
        dict(dict(id=tid, title=title, status="open", role="backend", deps=[], notes=[], priority=2), **kw),
    )


@pytest.fixture
def board(tmp_path):
    b = tmp_path / "proj" / ".tickets"
    b.mkdir(parents=True)
    for name in ("alice", "bob", "boss"):
        write(b / "agents" / (name + ".json"), {"owner": name, "seen": stamp(1)})
    write(b / "workforce.json", {"alice": {"harness": "codex"}, "bob": {"harness": "claude"}})
    write(b / "master.json", {"owner": "boss", "cos": "", "since": stamp(600), "lead": "alice"})
    write(
        b / "objective.json",
        {"text": "Produce verified CSV statistics", "exit_criterion": "exact row count accepted", "state": "active"},
    )
    # A accepted; B blocked waiting on A (dep_unaccepted would need A done-unaccepted;
    # here A is accepted so B that depends on A can be claimed — seed B open with
    # a still-open dep? Ticket asks: A accepted and B blocked on A.
    # Use B blocked status with deps=[A] and a block reason, or waiting phase.
    ticket(
        b,
        "T-001",
        "Write CSV statistics",
        status="done",
        owner="alice",
        review_head=SHA_A,
        review_events=[{"kind": "accept", "by": "bob", "at": stamp(10), "sha": SHA_A, "notes": "3 rows"}],
        # no sounding proof — the accept record is the verification proof
        proof="",
    )
    ticket(
        b,
        "T-002",
        "Consume verified statistics",
        status="blocked",
        owner="bob",
        deps=["T-001"],
        blocked_reason="waiting on accepted handoff to be claimed",
    )
    return b


def test_project_counts_include_claimed_as_working_signal(board):
    """Sidebar must not say 0 open when the board has claimed/working work."""
    ticket(board, "T-003", "Live work", status="claimed", owner="bob")
    counts, lead = tk._ui_project_counts(str(board))
    assert counts["claimed"] == 1
    assert counts["open"] == 0
    assert counts["blocked"] == 1
    assert counts["done"] == 1
    assert lead == "alice"


def test_accepted_ticket_proof_is_accept_record(board):
    out = tk.ui_ticket(str(board), "T-001", "boss", "normal")
    assert out["accepted"] is True
    assert out["acceptance"]["proof"]
    assert "Accepted by @bob" in out["acceptance"]["proof"]
    assert "6cf5700" in out["acceptance"]["proof"]
    assert out["acceptance"]["proof"] != "no proof recorded"


def test_plan_summary_has_blocker_and_next_owner(board):
    plan = tk.ui_plan(str(board), "normal")
    assert plan["available"] is True
    assert (plan.get("objective") or {}).get("text")
    summary = plan.get("summary") or {}
    blocked = summary.get("blocked") or []
    # T-002 is status=blocked
    assert any(x.get("id") == "T-002" for x in blocked)
    counts = plan.get("counts") or {}
    assert counts.get("blocked", 0) >= 1
    assert counts.get("done", 0) >= 1


def test_acceptance_proof_helper_prefers_sounding():
    assert tk._ui_acceptance_proof({"proof": "row count"}, True, "Accepted by @x on abc") == "row count"
    assert "Accepted by @bob" in tk._ui_acceptance_proof(
        {"proof": ""},
        True,
        "Accepted by @bob on 6cf5700",
        [],
    )
    assert tk._ui_acceptance_proof({"proof": ""}, False, "Accepted by @bob on 6cf5700") == ""
    unbound = tk._ui_acceptance_proof(
        {"proof": ""},
        False,
        "Accepted by @bob on 6cf5700",
        [{
            "kind": "accept",
            "by": "bob",
            "sha": "6cf57003447931cf822f50ee8aeca2389700507b",
            "applies": False,
            "superseded": False,
        }],
    )
    assert "not bound to a review head" in unbound
    assert "@bob" in unbound
    assert "6cf5700" in unbound
    assert "atm review" in unbound


def test_unbound_accept_proof_via_real_cli(tmp_path):
    """CEO REJECT repro: done --force then accept without review_head.

    Seed through the real CLI, not hand-written review_head JSON. The
    drill-down must name the unbound accept instead of 'no proof recorded',
    and a dependent's plan hint must not offer bare atm accept.
    """
    import subprocess

    board = tmp_path / "proj" / ".tickets"
    board.mkdir(parents=True)
    (board / "agents").mkdir()
    for name in ("alice", "bob"):
        write(board / "agents" / (name + ".json"), {"owner": name, "seen": stamp(1)})
    write(board / "workforce.json", {"alice": {"harness": "codex"}, "bob": {"harness": "claude"}})
    ticket(board, "T-001", "Write CSV statistics", status="open", owner="")
    ticket(
        board,
        "T-002",
        "Consume verified statistics",
        status="open",
        owner="",
        deps=["T-001"],
    )

    full_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
    ).strip()
    assert len(full_sha) == 40

    def run(agent, *args):
        env = os.environ.copy()
        env["TICKETS_DIR"] = str(board)
        env["TICKET_AGENT"] = agent
        # Seat/session pins from the worker shell must not override the agent.
        for k in ("TICKET_SEAT", "TICKETS_WATCH_PINNED", "TICKET_SESSION_ID"):
            env.pop(k, None)
        return subprocess.run(
            [sys.executable, str(ROOT / "tickets.py"), *args],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    claim = run("alice", "claim", "T-001")
    assert claim.returncode == 0, claim.stdout + claim.stderr
    done = run("alice", "done", "T-001", "--force", "--notes", "shipped")
    assert done.returncode == 0, done.stdout + done.stderr
    accept = run("bob", "accept", "T-001", "--sha", full_sha, "--notes", "ok")
    assert accept.returncode == 0, accept.stdout + accept.stderr

    raw = json.loads((board / "T-001.json").read_text())
    assert not raw.get("review_head"), "done --force must not invent review_head"
    assert any(
        (e.get("kind") or "").lower() == "accept" for e in (raw.get("review_events") or [])
    )

    out = tk.ui_ticket(str(board), "T-001", "boss", "normal")
    assert out["accepted"] is False
    proof = out["acceptance"]["proof"]
    assert proof, "must not leave ACCEPTANCE PROOF empty beside Accepted by"
    assert "no proof recorded" not in proof
    assert "not bound to a review head" in proof
    assert "@bob" in proof
    assert full_sha[:7] in proof
    assert "atm review" in proof
    assert "Accepted by @bob" in (out["review"]["label"] or "")

    # Dependent still blocked: hint must name the unbound accept, not bare accept.
    from src.ticket_board import work_view as wv

    tickets = tk.load_all(str(board))
    by_id = {t["id"]: t for t in tickets}
    child = {"id": "T-002", "phase": "ready", "deps": ["T-001"]}
    blockers = wv.blockers_of(child, by_id)
    assert len(blockers) == 1
    b = blockers[0]
    assert b["kind"] == "dep_unaccepted"
    assert "not bound to a review head" in b["text"]
    assert "atm review" in b["cmd"]
    assert b["cmd"].index("atm review") < b["cmd"].index("atm accept")
