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
        {"proof": "", "id": "T-001"},
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
    assert "atm reopen T-001" in unbound
    assert "then claim" in unbound
    assert "atm review" in unbound
    applying = tk._ui_acceptance_proof(
        {"proof": ""},
        False,
        "Accepted by @bob on 6cf5700",
        [{
            "kind": "accept",
            "by": "bob",
            "sha": "6cf57003447931cf822f50ee8aeca2389700507b",
            "applies": True,
            "superseded": False,
        }],
    )
    assert applying == "Accepted by @bob on 6cf5700 (not marked done yet)"
    # Newest unbound accept wins (not the first).
    newest = tk._ui_unbound_accept_message(
        [
            {
                "kind": "accept",
                "by": "alice",
                "sha": "1111111111111111111111111111111111111111",
                "applies": False,
                "superseded": False,
            },
            {
                "kind": "accept",
                "by": "bob",
                "sha": "6cf57003447931cf822f50ee8aeca2389700507b",
                "applies": False,
                "superseded": False,
            },
        ],
        tid="T-009",
    )
    assert "@bob" in newest and "6cf5700" in newest
    assert "@alice" not in newest


def _cli_board(tmp_path, *tids_titles):
    board = tmp_path / "proj" / ".tickets"
    board.mkdir(parents=True)
    (board / "agents").mkdir()
    for name in ("alice", "bob"):
        write(board / "agents" / (name + ".json"), {"owner": name, "seen": stamp(1)})
    write(board / "workforce.json", {"alice": {"harness": "codex"}, "bob": {"harness": "claude"}})
    for tid, title, extra in tids_titles:
        ticket(board, tid, title, status="open", owner="", **extra)
    return board


def _cli_run(board, agent, *args):
    import subprocess

    env = os.environ.copy()
    env["TICKETS_DIR"] = str(board)
    env["TICKET_AGENT"] = agent
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


def test_unbound_accept_proof_via_real_cli(tmp_path):
    """CEO REJECT repro: done --force then accept without review_head.

    Seed through the real CLI, not hand-written review_head JSON. The
    drill-down must name the unbound accept instead of 'no proof recorded',
    and a dependent's plan hint must offer reopen (not review-on-DONE).
    Then run that suggested path and prove it binds a review head.
    """
    import subprocess

    board = _cli_board(
        tmp_path,
        ("T-001", "Write CSV statistics", {}),
        ("T-002", "Consume verified statistics", {"deps": ["T-001"]}),
    )

    full_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
    ).strip()
    assert len(full_sha) == 40

    claim = _cli_run(board, "alice", "claim", "T-001")
    assert claim.returncode == 0, claim.stdout + claim.stderr
    done = _cli_run(board, "alice", "done", "T-001", "--force", "--notes", "shipped")
    assert done.returncode == 0, done.stdout + done.stderr
    accept = _cli_run(board, "bob", "accept", "T-001", "--sha", full_sha, "--notes", "ok")
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
    assert "atm reopen T-001" in proof
    assert "then claim" in proof
    assert "atm review" in proof
    assert "Accepted by @bob" in (out["review"]["label"] or "")

    # Dependent still blocked: hint must lead with reopen, not review-on-DONE.
    from src.ticket_board import work_view as wv

    tickets = tk.load_all(str(board))
    by_id = {t["id"]: t for t in tickets}
    child = {"id": "T-002", "phase": "ready", "deps": ["T-001"]}
    blockers = wv.blockers_of(child, by_id)
    assert len(blockers) == 1
    b = blockers[0]
    assert b["kind"] == "dep_unaccepted"
    assert "not bound to a review head" in b["text"]
    assert b["cmd"] == wv.unbound_accept_fix_cmd("T-001")
    assert "atm reopen T-001" in b["cmd"]
    assert b["cmd"].index("atm reopen") < b["cmd"].index("atm claim")
    assert b["cmd"].index("atm claim") < b["cmd"].index("atm review")
    assert b["cmd"].index("atm review") < b["cmd"].index("atm accept")

    # Prove review-on-DONE fails, and the suggested reopen path works.
    bad = _cli_run(board, "alice", "review", "T-001", "--force", "--notes", "cannot")
    assert bad.returncode != 0
    assert "DONE" in (bad.stderr + bad.stdout) or "done" in (bad.stderr + bad.stdout).lower()
    reopen = _cli_run(board, "bob", "reopen", "T-001", "--notes", "bind a review head")
    assert reopen.returncode == 0, reopen.stdout + reopen.stderr
    reclaim = _cli_run(board, "alice", "claim", "T-001")
    assert reclaim.returncode == 0, reclaim.stdout + reclaim.stderr
    review = _cli_run(board, "alice", "review", "T-001", "--force", "--notes", "paths")
    assert review.returncode == 0, review.stdout + review.stderr
    raw2 = json.loads((board / "T-001.json").read_text())
    head = (raw2.get("review_head") or "").strip()
    assert len(head) == 40, "reopen path must pin a full review_head"
    accept2 = _cli_run(board, "bob", "accept", "T-001", "--sha", head, "--notes", "bound")
    assert accept2.returncode == 0, accept2.stdout + accept2.stderr
    raw3 = json.loads((board / "T-001.json").read_text())
    assert wv.structured_accept(raw3)


def test_applying_accept_while_in_review_via_real_cli(tmp_path):
    """CEO REJECT repro: IN REVIEW + accept at head still shows proof.

    claim -> review --force -> accept --sha <head> leaves status=review so
    accepted is false, but the accept applies. ACCEPTANCE PROOF must show
    the applying accept, not 'no proof recorded'.
    """
    board = _cli_board(tmp_path, ("T-001", "Write CSV statistics", {}))

    claim = _cli_run(board, "alice", "claim", "T-001")
    assert claim.returncode == 0, claim.stdout + claim.stderr
    review = _cli_run(board, "alice", "review", "T-001", "--force", "--notes", "paths")
    assert review.returncode == 0, review.stdout + review.stderr
    raw = json.loads((board / "T-001.json").read_text())
    assert raw.get("status") == "review"
    head = (raw.get("review_head") or "").strip()
    assert len(head) == 40

    accept = _cli_run(board, "bob", "accept", "T-001", "--sha", head, "--notes", "ok")
    assert accept.returncode == 0, accept.stdout + accept.stderr
    raw2 = json.loads((board / "T-001.json").read_text())
    assert raw2.get("status") == "review", "accept must not mark done"

    out = tk.ui_ticket(str(board), "T-001", "boss", "normal")
    assert out["accepted"] is False
    assert out["status"] == "review"
    assert any(v.get("applies") for v in (out["review"]["verdicts"] or []))
    proof = out["acceptance"]["proof"]
    assert proof, "must not leave ACCEPTANCE PROOF empty beside Accepted by"
    assert "no proof recorded" not in proof
    assert "Accepted by @bob" in proof
    assert head[:7] in proof
    assert "not marked done yet" in proof
    assert "Accepted by @bob" in (out["review"]["label"] or "")
