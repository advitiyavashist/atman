"""T-416: tickets route --shadow --score retrospective scorecard."""

import json
import os
from pathlib import Path

from ticket_board.scheduler import (
    MIN_COMPARE,
    score_shadow,
    shadow_pick_at_claim,
)
from ticket_board.turns import load_trajectory_events
from test_wakeup import board, run  # noqa: F401

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "t349_adversarial_trajectories.jsonl"
ROOT = Path(__file__).resolve().parents[1]


def _join(board, name, role, model, cost, tool="claude"):
    r = run(board, "join", name, "--roles", role, "--model", model,
            "--cost", cost, "--tool", tool, agent=name)
    assert r.returncode == 0, r.stderr


def _stamp(board, tid, owner, role, pri, status="done"):
    path = board / ("%s.json" % tid)
    rec = json.loads(path.read_text()) if path.exists() else json.loads(
        (board / "T-001.json").read_text())
    rec.update({"id": tid, "owner": owner, "role": role, "priority": pri,
                "title": tid, "status": status, "deps": []})
    path.write_text(json.dumps(rec, indent=2))


def _event(kind, ticket, agent, model, at, **extra):
    rec = {"v": 1, "at": at, "kind": kind, "ticket": ticket, "agent": agent}
    if model:
        rec["model"] = model
    rec.update(extra)
    return rec


def _finish_with_turns(tid, agent, model, n_turns, day):
    evs = [_event("claim", tid, agent, model, "%sT10:00:00Z" % day)]
    for i in range(n_turns):
        evs.append(_event("run_start", tid, agent, model,
                          "%sT10:%02d:00Z" % (day, i + 1), run_no=i + 1))
        evs.append(_event("run_end", tid, agent, model,
                          "%sT11:%02d:00Z" % (day, i + 1), run_no=i + 1, exit=0))
    evs.append(_event("done", tid, agent, model, "%sT13:00:00Z" % day, outcome="done"))
    return evs


def _write_jsonl(board, events):
    (board / "trajectories.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events))


def _score_agent(board, name, entry, roles, ticket):
    from tickets import score_agent
    return score_agent(board, name, entry, roles, ticket)


def test_score_leakage_post_claim_record_ignored(board):
    """A record dated after claim must not change the shadow pick."""
    _join(board, "alice", "backend", "opus", "high")
    _join(board, "bob", "backend", "sonnet", "medium")
    events = []
    for i in range(5):
        tid = "T-5%02d" % i
        _stamp(board, tid, "alice", "backend", 1)
        events.extend(_finish_with_turns(tid, "alice", "opus", 2, "2026-08-%02d" % (i + 1)))
    for i in range(5):
        tid = "T-6%02d" % i
        _stamp(board, tid, "bob", "backend", 1)
        events.extend(_finish_with_turns(tid, "bob", "sonnet", 8, "2026-07-%02d" % (i + 1)))
    run(board, "create", "score target", "--role", "backend", "--priority", "1")
    target = None
    for p in board.glob("T-*.json"):
        rec = json.loads(p.read_text())
        if rec.get("title") == "score target":
            target = rec
            break
    assert target
    tid = target["id"]
    claim_at = "2026-09-10T10:00:00Z"
    events.extend(_finish_with_turns(tid, "bob", "sonnet", 4, "2026-09-10"))
    claim_at = "2026-09-10T10:00:00Z"
    _stamp(board, "T-599", "alice", "backend", 1)
    _write_jsonl(board, events)
    tickets = [json.loads(p.read_text()) for p in board.glob("T-*.json")]
    workforce = json.loads((board / "workforce.json").read_text())
    roles = json.loads((board / "roles.json").read_text())
    all_events = load_trajectory_events(str(board))
    pick_before, _ = shadow_pick_at_claim(
        str(board), target, all_events, claim_at,
        ["alice", "bob"], workforce, roles, _score_agent, tickets)
    poisoned = list(all_events) + [
        _event("done", "T-599", "alice", "opus", "2026-09-10T11:30:00Z", outcome="done"),
        _event("run_end", "T-599", "alice", "opus", "2026-09-10T11:30:00Z", run_no=1),
    ]
    pick_after, _ = shadow_pick_at_claim(
        str(board), target, poisoned, claim_at,
        ["alice", "bob"], workforce, roles, _score_agent, tickets)
    assert pick_before == "alice"
    assert pick_after == pick_before


def test_score_command_read_only(board):
    _join(board, "alice", "backend", "opus", "high")
    events = _finish_with_turns("T-001", "alice", "opus", 2, "2026-05-01")
    _stamp(board, "T-001", "alice", "backend", 1)
    _write_jsonl(board, events)
    before = (board / "trajectories.jsonl").read_text()
    r = run(board, "route", "--shadow", "--score", cwd=board.parent)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "shadow-vs-actual scorecard" in r.stdout
    assert "T-001" in r.stdout
    after = (board / "trajectories.jsonl").read_text()
    assert after == before
    assert "shadow_decision" not in after


def test_score_fixture_t349_two_rows(board):
    """Hand-verify two rows from the T-349 adversarial fixture."""
    _join(board, "composer", "backend", "opus", "high")
    _join(board, "sonnet-a", "backend", "sonnet", "medium")
    _stamp(board, "T-002", "composer", "backend", 2, status="done")
    _stamp(board, "T-003", "composer", "backend", 2, status="claimed")
    _stamp(board, "T-004", "composer", "backend", 2, status="done")
    events = [json.loads(ln) for ln in FIXTURE.read_text().splitlines() if ln.strip()]
    # second scorable row: alice finishes with bound run_start before T-002's claim
    _stamp(board, "T-010", "sonnet-a", "backend", 2, status="done")
    events = _finish_with_turns("T-010", "sonnet-a", "sonnet", 2, "2026-09-06") + events
    _write_jsonl(board, events)
    tickets = [json.loads(p.read_text()) for p in board.glob("T-*.json")]
    workforce = json.loads((board / "workforce.json").read_text())
    roles = json.loads((board / "roles.json").read_text())
    rep = score_shadow(
        load_trajectory_events(str(board)), tickets, workforce, roles,
        str(board), _score_agent)
    by_id = {r["ticket"]: r for r in rep["rows"]}
    assert "T-002" in by_id
    t2 = by_id["T-002"]
    assert t2["actual"] == "composer"
    assert t2["realized_turns"] == 3
    assert t2["claim_at"] == "2026-09-07T15:04:15Z"
    # n=0 at first claim: prior tier sonnet/medium -> sonnet-a, not composer
    assert t2["shadow"] == "sonnet-a"
    assert t2["agree"] is False
    assert "T-010" in by_id
    t10 = by_id["T-010"]
    assert t10["actual"] == "sonnet-a"
    assert t10["shadow"] == "sonnet-a"
    assert t10["agree"] is True
    assert "T-004" not in by_id  # backfill-only, no bound run_start
    docs = board.parent / "docs"
    docs.mkdir(exist_ok=True)
    r = run(board, "route", "--shadow", "--score",
            "--write-scorecard", str(docs / "turns-scorecard.md"),
            cwd=board.parent)
    assert r.returncode == 0, r.stderr
    assert (docs / "turns-scorecard.md").exists()


def test_disagreement_medians_null_under_min_compare(board):
    _join(board, "alice", "backend", "opus", "high")
    _join(board, "bob", "backend", "sonnet", "medium")
    events = []
    for i in range(2):
        tid = "T-7%02d" % i
        _stamp(board, tid, "alice", "backend", 1)
        events.extend(_finish_with_turns(tid, "alice", "opus", 2, "2026-08-%02d" % (i + 1)))
    for i in range(2):
        tid = "T-8%02d" % i
        _stamp(board, tid, "bob", "backend", 1)
        events.extend(_finish_with_turns(tid, "bob", "sonnet", 8, "2026-07-%02d" % (i + 1)))
    run(board, "create", "disagree", "--role", "backend", "--priority", "1")
    target = json.loads((sorted(board.glob("T-*.json"))[-1]).read_text())
    events.extend(_finish_with_turns(target["id"], "bob", "sonnet", 5, "2026-09-11"))
    _write_jsonl(board, events)
    tickets = [json.loads(p.read_text()) for p in board.glob("T-*.json")]
    workforce = json.loads((board / "workforce.json").read_text())
    roles = json.loads((board / "roles.json").read_text())
    rep = score_shadow(
        load_trajectory_events(str(board)), tickets, workforce, roles,
        str(board), _score_agent)
    row = [r for r in rep["rows"] if r["ticket"] == target["id"]][0]
    assert row["actual_median"] is None
    assert row["shadow_median"] is None
    assert row["actual_n"] < MIN_COMPARE
    assert row["shadow_n"] < MIN_COMPARE
