"""T-484: eligibility filter before shadow/route scoring."""

import json
from datetime import datetime, timezone, timedelta

from ticket_board.scheduler import (
    DEFAULT_ALIVE_WITHIN_MIN,
    filter_eligible,
    shadow_pick_at_claim,
)
from ticket_board.turns import load_trajectory_events
from test_wakeup import board, run  # noqa: F401


def _join(board, name, role, model="sonnet", cost="medium"):
    r = run(board, "join", name, "--roles", role, "--model", model,
            "--cost", cost, agent=name)
    assert r.returncode == 0, r.stderr


def _stamp_agent_seen(board, name, at):
    agents = board / "agents"
    agents.mkdir(exist_ok=True)
    path = agents / ("%s.json" % name)
    rec = json.loads(path.read_text()) if path.exists() else {"owner": name}
    rec["seen"] = at
    path.write_text(json.dumps(rec, indent=2))


def _limit_agent(board, name, at):
    agents = board / "agents"
    agents.mkdir(exist_ok=True)
    path = agents / ("%s.json" % name)
    rec = json.loads(path.read_text()) if path.exists() else {"owner": name}
    rec["limit"] = {"at": at, "until": "", "note": "test"}
    path.write_text(json.dumps(rec, indent=2))


def _event(kind, ticket, agent, at, **extra):
    rec = {"v": 1, "at": at, "kind": kind, "ticket": ticket, "agent": agent}
    rec.update(extra)
    return rec


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _score_agent(board, name, entry, roles, ticket):
    from tickets import score_agent
    return score_agent(board, name, entry, roles, ticket)


def test_dormant_seat_excluded(board):
    _join(board, "alice", "backend")
    _stamp_agent_seen(board, "alice", _ago(120))
    wf = json.loads((board / "workforce.json").read_text())
    roles = json.loads((board / "roles.json").read_text())
    agents = {"alice": json.loads((board / "agents" / "alice.json").read_text())}
    eligible, counts = filter_eligible(
        ["alice"], wf, roles, agents, {}, {},
        alive_within_min=DEFAULT_ALIVE_WITHIN_MIN)
    assert eligible == []
    assert counts["dormant"] == 1


def test_limited_seat_excluded(board):
    _join(board, "bob", "backend")
    _stamp_agent_seen(board, "bob", _now())
    _limit_agent(board, "bob", _now())
    wf = json.loads((board / "workforce.json").read_text())
    roles = json.loads((board / "roles.json").read_text())
    agents = {"bob": json.loads((board / "agents" / "bob.json").read_text())}
    eligible, counts = filter_eligible(["bob"], wf, roles, agents, {}, {})
    assert eligible == []
    assert counts["limit"] == 1


def test_default_roles_only_name_excluded(board):
    """claude is in DEFAULT_ROLES but not workforce — never a candidate."""
    default_roles = {"claude": ["backend"], "cursor": ["verification"]}
    eligible, counts = filter_eligible(
        ["claude"], {}, {}, {}, {}, default_roles)
    assert eligible == []
    assert counts["unregistered"] == 1


def test_live_registered_seat_retained(board):
    _join(board, "carol", "backend")
    _stamp_agent_seen(board, "carol", _now())
    wf = json.loads((board / "workforce.json").read_text())
    roles = json.loads((board / "roles.json").read_text())
    agents = {"carol": json.loads((board / "agents" / "carol.json").read_text())}
    eligible, counts = filter_eligible(["carol"], wf, roles, agents, {}, {})
    assert eligible == ["carol"]
    assert counts["total"] == 0


def test_claim_time_uses_event_before_claim_not_later(board):
    _join(board, "alice", "backend", model="sonnet", cost="medium")
    _join(board, "bob", "backend", model="sonnet", cost="medium")
    ticket = json.loads((board / "T-001.json").read_text())
    ticket["role"] = "backend"
    (board / "T-001.json").write_text(json.dumps(ticket, indent=2))
    claim_at = "2026-09-10T12:00:00Z"
    events = [
        # alice active within 90m before claim
        _event("run_start", "T-099", "alice", "2026-09-10T11:30:00Z", run_no=1),
        _event("run_end", "T-099", "alice", "2026-09-10T11:35:00Z", run_no=1, exit=0),
        _event("claim", "T-001", "bob", claim_at),
        _event("run_start", "T-001", "bob", "2026-09-10T12:01:00Z", run_no=1),
        _event("run_end", "T-001", "bob", "2026-09-10T12:02:00Z", run_no=1, exit=0),
        # post-claim activity must not revive alice for this pick
        _event("run_start", "T-099", "alice", "2026-09-10T13:00:00Z", run_no=2),
    ]
    (board / "trajectories.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events))
    wf = json.loads((board / "workforce.json").read_text())
    roles = json.loads((board / "roles.json").read_text())
    tickets = [ticket]
    all_events = load_trajectory_events(str(board))
    pick, _ = shadow_pick_at_claim(
        str(board), ticket, all_events, claim_at,
        ["alice", "bob"], wf, roles, _score_agent, tickets,
        alive_within_min=90)
    assert pick == "alice"


def test_busy_seat_excluded(board):
    _join(board, "dave", "backend")
    _stamp_agent_seen(board, "dave", _now())
    wf = json.loads((board / "workforce.json").read_text())
    roles = json.loads((board / "roles.json").read_text())
    agents = {"dave": json.loads((board / "agents" / "dave.json").read_text())}
    eligible, counts = filter_eligible(["dave"], wf, roles, agents, {"dave": 1}, {})
    assert eligible == []
    assert counts["busy"] == 1


def test_route_shadow_prints_excluded_header(board):
    _join(board, "eve", "backend")
    _stamp_agent_seen(board, "eve", _now())
    r = run(board, "route", "--shadow", cwd=board.parent)
    assert r.returncode == 0, r.stderr
    assert "excluded" in r.stdout


def test_shadow_score_json_honoured(board):
    _join(board, "frank", "backend")
    _stamp_agent_seen(board, "frank", _now())
    events = [
        _event("claim", "T-001", "frank", "2026-05-01T10:00:00Z"),
        _event("run_start", "T-001", "frank", "2026-05-01T10:01:00Z", run_no=1, sha="8f513fe"),
        _event("run_end", "T-001", "frank", "2026-05-01T10:02:00Z", run_no=1, exit=0),
        _event("done", "T-001", "frank", "2026-05-01T11:00:00Z", outcome="done"),
    ]
    (board / "trajectories.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events))
    ticket = json.loads((board / "T-001.json").read_text())
    ticket.update({"owner": "frank", "status": "done", "role": "backend"})
    (board / "T-001.json").write_text(json.dumps(ticket, indent=2))
    r = run(board, "route", "--shadow", "--score", "--json", cwd=board.parent)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["v"] == 1
    assert "agreement_rate" in data
    assert "n_live_subset" in data
    assert "rows" in data


def test_two_copy_parity_route_excluded(board):
    """Root tickets.py and package cli share the same exclusion header."""
    _join(board, "gina", "backend")
    _stamp_agent_seen(board, "gina", _ago(200))
    r1 = run(board, "route", "--shadow", cwd=board.parent)
    import subprocess
    import sys
    from pathlib import Path
    pkg = Path(__file__).resolve().parents[1] / "src"
    env = dict(__import__("os").environ, TICKETS_DIR=str(board), PYTHONPATH=str(pkg))
    r2 = subprocess.run(
        [sys.executable, "-m", "ticket_board.cli", "route", "--shadow"],
        capture_output=True, text=True, env=env, cwd=board.parent)
    assert r1.returncode == 0 and r2.returncode == 0
    assert r1.stdout.splitlines()[0] == r2.stdout.splitlines()[0]
