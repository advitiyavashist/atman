"""T-480: read-time cost estimates from tokens x public price table.

Synthetic fixtures only (T-392 isolation): never reads the live jsonl.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board, run  # noqa: F401
from test_trajectories import worked  # noqa: F401
from ticket_board.prices import (
    estimate_run_end_cost,
    fmt_cost_cell,
    load_price_table,
    ticket_cost_fields,
)
from ticket_board.turns import build_turns_report

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _write_events(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _run_end(ticket, model, tokens_in=None, tokens_out=None, cost_usd=None, **extra):
    e = {"v": 1, "at": "2026-09-08T02:00:00Z", "kind": "run_end",
         "ticket": ticket, "agent": "alice", "model": model}
    if tokens_in is not None:
        e["tokens_in"] = tokens_in
    if tokens_out is not None:
        e["tokens_out"] = tokens_out
    if cost_usd is not None:
        e["cost_usd"] = cost_usd
        e["cost_source"] = "harness"
    e.update(extra)
    return e


def test_zero_tokens_is_not_zero_dollars():
    ev = _run_end("T-1", "sonnet", tokens_in=0, tokens_out=0)
    assert estimate_run_end_cost(ev) == (None, None, None)


def test_known_model_with_tokens_gets_estimate():
    ev = _run_end("T-1", "opus", tokens_in=1_000_000, tokens_out=0)
    cost, source, as_of = estimate_run_end_cost(ev)
    assert cost == 5.0 and source == "estimate" and as_of == "2026-09-01"


def test_unknown_model_stays_unmeasured():
    ev = _run_end("T-1", "composer-2.5", tokens_in=100, tokens_out=200)
    assert estimate_run_end_cost(ev) == (None, None, None)


def test_tokens_absent_stays_unmeasured():
    ev = _run_end("T-1", "opus")
    assert estimate_run_end_cost(ev) == (None, None, None)


def test_harness_cost_is_not_overwritten():
    ev = _run_end("T-1", "opus", tokens_in=100, tokens_out=100, cost_usd=0.99)
    assert estimate_run_end_cost(ev) == (None, None, None)


def test_mixed_harness_and_est_ticket_fields():
    evs = [
        _run_end("T-1", "opus", tokens_in=100, tokens_out=0, cost_usd=0.10),
        _run_end("T-1", "opus", tokens_in=1_000_000, tokens_out=0),
    ]
    harness, est, source, _ = ticket_cost_fields(evs, 0.10)
    assert harness == 0.10 and source == "harness"
    assert est == 5.0
    assert fmt_cost_cell(harness, est) == "$0.1000 + $5.0000 est"


def test_turns_json_shows_est_on_priced_rows(worked):
    b = worked
    evs = [
        {"v": 1, "at": "2026-09-08T01:00:00Z", "kind": "claim", "ticket": "T-900",
         "agent": "cos-opus"},
        _run_end("T-900", "opus", tokens_in=50, tokens_out=16758),
    ]
    _write_events(b / "trajectories.jsonl", evs)
    rep = build_turns_report(evs, tickets=[{"id": "T-900", "status": "claimed"}])
    row = rep["tickets"][0]
    assert row["cost_usd"] is None
    assert row["cost_usd_est"] == pytest.approx(0.4192, rel=1e-3)
    assert row["cost_source"] == "estimate"
    assert rep["aggregates"]["cost"]["n"] == 0
    assert rep["aggregates"]["cost_est"]["n"] == 1


def test_turns_json_cursor_without_tokens_stays_dash(worked):
    b = worked
    evs = [
        {"v": 1, "at": "2026-09-08T01:00:00Z", "kind": "claim", "ticket": "T-901",
         "agent": "composer"},
        _run_end("T-901", "composer-2.5"),
    ]
    _write_events(b / "trajectories.jsonl", evs)
    rep = build_turns_report(evs, tickets=[{"id": "T-901", "status": "claimed"}])
    row = rep["tickets"][0]
    assert row["cost_usd"] is None
    assert row.get("cost_usd_est") is None
    assert row.get("cost_source") is None


def test_turns_table_labels_est(worked):
    b = worked
    repo = b.parent
    evs = [
        {"v": 1, "at": "2026-09-08T01:00:00Z", "kind": "claim", "ticket": "T-902",
         "agent": "cos-opus"},
        _run_end("T-902", "sonnet", tokens_in=1_000_000, tokens_out=0),
    ]
    _write_events(b / "trajectories.jsonl", evs)
    r = run(b, "turns", agent="alice", cwd=repo)
    assert " est" in r.stdout
    assert "$3.0000 est" in r.stdout


def test_both_copies_render_same_est_summary(worked, tmp_path):
    b = worked
    repo = b.parent
    evs = [
        {"v": 1, "at": "2026-09-08T01:00:00Z", "kind": "claim", "ticket": "T-903",
         "agent": "cos-opus"},
        _run_end("T-903", "opus", tokens_in=1_000_000, tokens_out=0),
    ]
    _write_events(b / "trajectories.jsonl", evs)
    root = run(b, "trajectories", "--summary", agent="alice", cwd=repo).stdout
    e = dict(os.environ, TICKETS_DIR=str(b), TICKET_AGENT="alice",
             HOME=str(b.parent.parent / "home"),
             PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    e.pop("TICKETS_STOP_HOOK", None)
    pkg = subprocess.run([sys.executable, "-m", "ticket_board", "trajectories", "--summary"],
                         capture_output=True, text=True, env=e, cwd=repo)
    assert pkg.returncode == 0, pkg.stderr

    def cost_cols(out):
        return [" ".join(l.split()[6:8]) for l in out.splitlines() if l.startswith("T-90")]

    assert cost_cols(root) == cost_cols(pkg.stdout) == ["$5.0000 est"]


def test_price_table_rows_have_public_citations():
    doc = load_price_table()
    for name, row in doc["models"].items():
        assert row.get("source", "").startswith("https://"), name
        assert row.get("as_of"), name
