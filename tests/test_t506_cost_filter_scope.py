"""T-506: per-run cost aggregates must respect --epic/--model filters.

Synthetic fixtures only. The five-row measurement from cos-opus's T-499 verdict
must reconcile under every filter; --model opus must not return a sonnet row in
cost_est_by_run_model.
"""

import pytest

from ticket_board.turns import build_turns_report


def _run_end(ticket, model, tokens_in=1_000_000, tokens_out=0, agent="alice", **extra):
    e = {
        "v": 1,
        "at": "2026-09-08T02:00:00Z",
        "kind": "run_end",
        "ticket": ticket,
        "agent": agent,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }
    e.update(extra)
    return e


def _tickets():
    return [
        {"id": "T-E11", "status": "claimed", "epic": "E-011", "owner": "alice"},
        {"id": "T-E10", "status": "claimed", "epic": "E-010", "owner": "bob"},
    ]


def _fixture_events():
    return [
        _run_end("T-E11", "opus", agent="alice"),
        _run_end("T-E10", "sonnet", agent="bob"),
        _run_end(None, "opus", agent="cos-opus"),
    ]


def _reconciles(aggs):
    ticket = aggs["cost_est"].get("total") or 0.0
    unbound = aggs["cost_est_unbound"].get("total") or 0.0
    by_run = sum(b.get("total") or 0.0 for b in aggs["cost_est_by_run_model"])
    return ticket + unbound == pytest.approx(by_run, rel=1e-6)


def test_unfiltered_cost_est_reconciles():
    rep = build_turns_report(_fixture_events(), tickets=_tickets())
    assert _reconciles(rep["aggregates"])


def test_agent_filter_reconciles():
    rep = build_turns_report(
        _fixture_events(), tickets=_tickets(), agent="alice")
    assert _reconciles(rep["aggregates"])


def test_epic_filter_reconciles_and_excludes_unbound():
    rep = build_turns_report(
        _fixture_events(), tickets=_tickets(), epic="E-011")
    aggs = rep["aggregates"]
    assert _reconciles(aggs)
    assert aggs["cost_est_unbound"]["n"] == 0
    assert aggs["cost_est_unbound"]["total"] is None
    assert aggs["cost_est_unbound"].get("excluded") == "unbound runs carry no epic"
    assert {r["ticket"] for r in rep["tickets"]} == {"T-E11"}


def test_epic_e010_reconciles():
    rep = build_turns_report(
        _fixture_events(), tickets=_tickets(), epic="E-010")
    assert _reconciles(rep["aggregates"])


def test_model_opus_filters_run_aggregate_not_sonnet():
    rep = build_turns_report(
        _fixture_events(), tickets=_tickets(), model="opus")
    aggs = rep["aggregates"]
    assert _reconciles(aggs)
    models = {b["model"] for b in aggs["cost_est_by_run_model"]}
    assert models == {"opus"}
    assert "sonnet" not in models


def test_model_opus_ticket_rows_use_ticket_model_axis():
    """Ticket row model comes from events; per-run aggregate uses run_end model."""
    evs = [
        {"v": 1, "at": "2026-09-08T01:00:00Z", "kind": "claim",
         "ticket": "T-910", "agent": "alice"},
        _run_end("T-910", "sonnet"),
        {"v": 1, "at": "2026-09-08T02:00:00Z", "kind": "update",
         "ticket": "T-910", "agent": "cos-opus", "model": "opus", "notes_len": 10},
    ]
    rep = build_turns_report(
        evs, tickets=[{"id": "T-910", "status": "claimed", "epic": "E-011"}],
        model="opus")
    assert len(rep["tickets"]) == 1
    assert rep["tickets"][0]["model"] == "opus"
    assert rep["aggregates"]["cost_est_by_run_model"] == []


def test_scope_block_names_filters_and_axes():
    rep = build_turns_report(
        _fixture_events(), tickets=_tickets(), epic="E-011", model="opus")
    scope = rep["scope"]
    assert scope["filters"] == {"epic": "E-011", "model": "opus"}
    assert scope["fields"]["cost_est_by_run_model"]["model_axis"] == "run_end model"
    assert scope["fields"]["cost_est"]["model_axis"] == "resolved ticket model"
    assert scope["fields"]["cost_est_unbound"]["excluded"] == (
        "unbound runs carry no epic")
