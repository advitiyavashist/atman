"""T-1073: agent-map follow-ups from the #243 review."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from test_t1072_agent_map import run_pair, stamp, tk_module  # noqa: F401
from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def _map():
    sys.path.insert(0, str(ROOT / "src"))
    from ticket_board import agent_map
    return agent_map


def test_dry_run_end_does_not_leave_unflagged_start_stalled():
    """run_start omitted dry_run; run_end has dry_run:true. No phantom row."""
    agent_map = _map()
    ev = [
        {"v": 1, "at": "2026-09-01T00:00:00Z", "kind": "run_start",
         "agent": "labseat", "ticket": "T-012", "run_no": 1, "run_id": "r-lab-1"},
        {"v": 1, "at": "2026-09-01T00:01:00Z", "kind": "run_end",
         "agent": "labseat", "ticket": "T-012", "run_no": 1, "run_id": "r-lab-1",
         "dry_run": True, "exit": 0},
    ]
    data = agent_map.build(
        [{"id": "T-012", "title": "lab", "owner": "labseat"}], ev, {}, {},
        show_all=True)
    assert data["runs"] == 0
    assert data["groups"] == []
    assert data["running"] == 0


def test_legacy_run_numbers_that_restart_stay_separate_rows():
    """Two eras reused run_no=1 with no run_id. --all must keep both."""
    agent_map = _map()
    ev = [
        {"v": 1, "at": "2026-01-01T00:00:00Z", "kind": "run_start",
         "agent": "old", "ticket": "T-010", "run_no": 1, "harness": "claude"},
        {"v": 1, "at": "2026-01-01T00:10:00Z", "kind": "run_end",
         "agent": "old", "ticket": "T-010", "run_no": 1, "exit": 0},
        {"v": 1, "at": "2026-09-01T00:00:00Z", "kind": "run_start",
         "agent": "old", "ticket": "T-011", "run_no": 1, "harness": "claude"},
        {"v": 1, "at": "2026-09-01T00:10:00Z", "kind": "run_end",
         "agent": "old", "ticket": "T-011", "run_no": 1, "exit": 0},
    ]
    data = agent_map.build(
        [{"id": "T-010", "title": "era1", "owner": "old"},
         {"id": "T-011", "title": "era2", "owner": "old"}],
        ev, {}, {}, show_all=True)
    assert data["runs"] == 2
    assert {g["ticket"] for g in data["groups"]} == {"T-010", "T-011"}
    assert all(g["runs"] == 1 for g in data["groups"])


def test_legacy_start_and_end_without_run_id_still_pair():
    """The #243 receipt fixture: one start+end, no run_id, stays one row."""
    agent_map = _map()
    ev = run_pair("rem", "T-009", 1, 20, 10, input_tokens=7, output_tokens=3)
    for e in ev:
        e.pop("run_id")
    data = agent_map.build(
        [{"id": "T-009", "title": "x", "owner": "rem"}], ev, {}, {}, show_all=True)
    assert data["runs"] == 1
    assert data["groups"][0]["rows"][0]["state"] == "done"


def test_read_only_liveness_deletes_unset_attributes(tmp_path, monkeypatch):
    tk = tk_module(monkeypatch, tmp_path / "home")
    table = tk._WATCH_TABLE
    for k in ("bound", "rows", "available", "cwds", "read_only"):
        if hasattr(table, k):
            delattr(table, k)
    with tk._read_only_liveness():
        assert table.read_only is True
        assert table.bound is True
        assert table.rows == []
    assert not hasattr(table, "read_only")
    assert not hasattr(table, "bound")
    assert not hasattr(table, "rows")

    with tk._shared_watch_table():
        assert table.bound is True
        with tk._read_only_liveness():
            assert table.read_only is True
            assert table.rows == []
        assert not hasattr(table, "read_only")
        assert table.bound is True
        assert table.rows is not None
