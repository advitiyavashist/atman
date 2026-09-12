"""T-801: Atman: turns and cost stay blank until a done ticket reports.

Audit tickets turns JSON, tickets ui hero, and Atman landing per-turn copy
so median turns and cost stay — / null until a done ticket has a counted
run_end (turns) and a harness-reported cost_usd (cost). n_unmeasured must be visible.
"""

import json
import shutil
from pathlib import Path

import pytest

from ticket_board.turns import build_turns_report, ROW_KEYS
from test_wakeup import board, run  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
LANDING = ROOT / "landing" / "index.html"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "turns_trajectories.jsonl"


def test_turns_json_no_measured_rows_median_null_n_unmeasured_positive():
    """Proof: tickets turns --json on a window with no done+measured rows shows
    turns null / n_unmeasured > 0, never 0 median sold as a measurement."""
    # Window with backfill / unmeasured tickets (no run_end events)
    evs = [
        {"kind": "claim", "ticket": "T-101", "agent": "alice", "at": "2026-09-08T00:00:00Z"},
        {"kind": "update", "ticket": "T-101", "agent": "alice", "at": "2026-09-08T00:01:00Z"},
        {"kind": "done", "ticket": "T-101", "agent": "alice", "at": "2026-09-08T00:02:00Z"},
        {"kind": "claim", "ticket": "T-102", "agent": "bob", "at": "2026-09-08T00:03:00Z"},
        {"kind": "done", "ticket": "T-102", "agent": "bob", "at": "2026-09-08T00:04:00Z"},
    ]
    report = build_turns_report(evs)
    assert report["v"] == 1
    assert len(report["tickets"]) == 2
    for r in report["tickets"]:
        assert r["turns"] is None
        assert r["cost_usd"] is None
        assert tuple(r.keys()) == ROW_KEYS

    agg = report["aggregates"]
    assert agg["n"] == 0
    assert agg["n_unmeasured"] == 2
    assert agg["mean"] is None
    assert agg["median"] is None  # JSON null, NEVER 0 or 0.0

    cost = agg["cost"]
    assert cost["n"] == 0
    assert cost["n_unmeasured"] == 2
    assert cost["mean"] is None
    assert cost["median"] is None
    assert cost["total"] is None  # NEVER 0.00


def test_promise_hero_median_turns_null_until_done_ticket_reports():
    """tickets ui hero: median turns stays None until a done ticket has a counted run_end."""
    import sys
    sys.path.insert(0, str(ROOT))
    import tickets as t_mod

    # Case 1: In-progress ticket has runs, but NO done ticket has runs
    turns_data = {
        "tickets": [
            {"ticket": "T-001", "outcome": "in-progress", "turns": 3, "cost_usd": None},
        ],
        "aggregates": {"median": 3.0, "n": 1, "n_unmeasured": 0},
    }
    hero = t_mod._promise_hero(turns_data, [{"id": "T-001", "status": "claimed"}], [])
    # Median turns MUST stay None because no done ticket reported turns
    assert hero["median_turns"] is None
    assert hero["cost_usd"] is None
    assert hero["yield_per_usd"] is None

    # Case 2: Done ticket reports counted run_end
    turns_done = {
        "tickets": [
            {"ticket": "T-001", "outcome": "done", "turns": 2, "cost_usd": None},
        ],
        "aggregates": {"median": 2.0, "n": 1, "n_unmeasured": 0},
    }
    hero_done = t_mod._promise_hero(turns_done, [{"id": "T-001", "status": "done"}], [])
    assert hero_done["median_turns"] == 2.0

    # Case 3: Done ticket with harness cost
    cost_event = {"kind": "run_end", "ticket": "T-001", "cost_usd": 0.50}
    hero_costed = t_mod._promise_hero(turns_done, [{"id": "T-001", "status": "done"}], [cost_event])
    assert hero_costed["median_turns"] == 2.0
    assert hero_costed["cost_usd"] == 0.50
    assert hero_costed["done_with_cost"] == 1
    assert hero_costed["yield_per_usd"] == 2.0


def test_landing_per_turn_copy_honesty():
    """Atman landing per-turn copy audit: median turns and cost stay — until a done ticket reports."""
    assert LANDING.is_file(), "landing/index.html must exist"
    html = LANDING.read_text()

    # Must contain the explicit honesty promise copy
    assert "Fewest turns and measured cost stay blank until a finished ticket reports them; unknown is not zero." in html
    assert "Unknown is not zero until a done ticket reports." in html
    assert "Median turns and measured cost stay blank on purpose." in html
    assert "Unknown until a done ticket reports. Unknown is not zero." in html

    # The metric definitions must display emdash —
    # Both in feature overview and per-turn efficiency panel
    assert "<dt>median turns</dt>" in html
    assert "<dt>measured cost</dt>" in html
    assert html.count("<dd>—</dd>") >= 4

    # Must not contain fake/invented numbers in promise/per-turn panels
    banned_fake_metrics = ["<dd>0</dd>", "<dd>0.0</dd>", "<dd>$0</dd>", "<dd>$0.00</dd>"]
    for b in banned_fake_metrics:
        assert b not in html, f"Landing HTML contains forbidden fake measurement: {b}"
