"""T-415: shadow scheduler ranks turns then measured cost."""

import json
from pathlib import Path

from ticket_board.scheduler import (
    build_estimates,
    learned_ranking,
)
from ticket_board.turns import load_trajectory_events
from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"
ROOT = TOOL.parent


def _join(board, name, role, model, cost, tool="claude"):
    r = run(board, "join", name, "--roles", role, "--model", model,
            "--cost", cost, "--tool", tool, agent=name)
    assert r.returncode == 0, r.stderr


def _stamp_done(board, tid, owner, role, pri, status="done"):
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


def _finish_with_turns(tid, agent, model, n_turns, day, cost_usd=None):
    evs = [_event("claim", tid, agent, model, "%sT10:00:00Z" % day)]
    for i in range(n_turns):
        extra = {}
        if cost_usd is not None:
            extra["cost_usd"] = cost_usd
        evs.append(_event("run_start", tid, agent, model,
                          "%sT10:%02d:00Z" % (day, i + 1), run_no=i + 1))
        evs.append(_event("run_end", tid, agent, model,
                          "%sT11:%02d:00Z" % (day, i + 1), run_no=i + 1, exit=0,
                          bound_write=True, **extra))
    evs.append(_event("done", tid, agent, model, "%sT13:00:00Z" % day, outcome="done"))
    return evs


def _write_jsonl(board, events):
    (board / "trajectories.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events))


def _load_board_ctx(board):
    tickets = [json.loads(p.read_text()) for p in board.glob("T-*.json")]
    wf = json.loads((board / "workforce.json").read_text())
    return tickets, wf


def _estimates(board):
    tickets, wf = _load_board_ctx(board)
    return build_estimates(load_trajectory_events(str(board)), tickets=tickets, workforce=wf)


def _seed_pair(board, alice_turns, bob_turns, alice_cost_per_run, bob_cost_per_run):
    _join(board, "alice", "backend", "opus", "high")
    _join(board, "bob", "backend", "sonnet", "medium")
    events = []
    for i in range(5):
        tid = "T-A%02d" % i
        _stamp_done(board, tid, "alice", "backend", 1)
        events.extend(_finish_with_turns(tid, "alice", "opus", alice_turns,
                                         "2026-08-%02d" % (i + 1), alice_cost_per_run))
    for i in range(5):
        tid = "T-B%02d" % i
        _stamp_done(board, tid, "bob", "backend", 1)
        events.extend(_finish_with_turns(tid, "bob", "sonnet", bob_turns,
                                         "2026-07-%02d" % (i + 1), bob_cost_per_run))
    _write_jsonl(board, events)


def test_null_cost_not_treated_as_cheapest(board):
    """Mixed fixture: measured cost beats same-turns agent with null cost."""
    _seed_pair(board, 2, 2, 0.25, None)
    run(board, "create", "ready cost", "--role", "backend", "--priority", "1")
    est = _estimates(board)
    ranked = learned_ranking(est, "backend", "p1")
    assert len(ranked) == 2
    assert ranked[0]["agent"] == "alice"
    assert ranked[1]["agent"] == "bob"
    assert ranked[0]["median_cost_usd"] == 0.50
    assert ranked[1]["median_cost_usd"] is None
    assert ranked[1]["n_cost"] == 0


def test_cost_tiebreak_picks_lower_cost(board):
    _seed_pair(board, 2, 2, 0.25, 0.05)
    run(board, "create", "ready cheap", "--role", "backend", "--priority", "1")
    est = _estimates(board)
    ranked = learned_ranking(est, "backend", "p1")
    assert ranked[0]["agent"] == "bob"
    assert ranked[0]["median_cost_usd"] == 0.10


def test_all_cost_unmeasured_note(board):
    _seed_pair(board, 2, 8, None, None)
    run(board, "create", "ready unmeas", "--role", "backend", "--priority", "1")
    r = run(board, "route", "--shadow", cwd=board.parent)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "cost: unmeasured (n=0)" in r.stdout
    assert "alice" in r.stdout


def test_by_cost_inverts_ranking(board):
    """Cost-first ranks bob cheaper despite more turns."""
    _join(board, "alice", "backend", "opus", "high")
    _join(board, "bob", "backend", "sonnet", "medium")
    events = []
    for i in range(5):
        tid = "T-C%02d" % i
        _stamp_done(board, tid, "alice", "backend", 1)
        events.extend(_finish_with_turns(tid, "alice", "opus", 2,
                                         "2026-08-%02d" % (i + 1), 0.25))
    for i in range(5):
        tid = "T-D%02d" % i
        _stamp_done(board, tid, "bob", "backend", 1)
        events.extend(_finish_with_turns(tid, "bob", "sonnet", 4,
                                         "2026-07-%02d" % (i + 1), 0.05))
    _write_jsonl(board, events)
    est = _estimates(board)
    by_turns = learned_ranking(est, "backend", "p1", rank_by="turns")
    by_cost = learned_ranking(est, "backend", "p1", rank_by="cost")
    assert by_turns[0]["agent"] == "alice"
    assert by_cost[0]["agent"] == "bob"


def test_shadow_json_additive_cost_keys(board):
    _seed_pair(board, 2, 8, 0.25, None)
    run(board, "create", "ready json", "--role", "backend", "--priority", "1")
    r = run(board, "route", "--shadow", "--json", cwd=board.parent)
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    assert doc["v"] == 1
    assert "decisions" in doc and "estimates" in doc
    row = [d for d in doc["decisions"] if d.get("title") != "ready json" or d.get("ticket")]
    learned = [d for d in doc["decisions"] if d.get("source") == "learned"]
    assert learned
    assert "expected_cost_usd" in learned[0]
    assert "n_cost" in learned[0]
    assert doc["estimates"]["n_cost_measured"] >= 5


def test_decide_ticket_never_picks_null_cost_over_measured(board):
    _seed_pair(board, 2, 2, 0.25, None)
    run(board, "create", "ready decide", "--role", "backend", "--priority", "1")
    tickets, wf = _load_board_ctx(board)
    ready = [t for t in tickets if t.get("status") == "open" and t.get("role") == "backend"][0]
    est = _estimates(board)
    ranked = learned_ranking(est, "backend", "p1")
    assert ranked and ranked[0]["agent"] == "alice"
    assert ranked[0]["median_cost_usd"] == 0.50
    assert ranked[1]["n_cost"] == 0
