"""T-792: CEO/worker loop shares one process-table snapshot.

`tickets who` and `health` (used by `tickets master` and master wake) used to
call `_parse_watch_table` once per seat. On a board with N agents that is
O(N) `ps` scans per pulse. The UI path already wrapped `board_snapshot`; the
CLI did not.

Throwaway board only. Metric is parse-count (and wall-clock on the live
board), not a fabricated `tickets turns` number.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


@pytest.fixture(scope="module")
def tk():
    spec = importlib.util.spec_from_file_location("tickets_under_test_t792", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def board(tmp_path):
    b = tmp_path / "repo" / ".tickets"
    b.mkdir(parents=True)
    (b / ".fixture-board").write_text("t792\n")
    return b


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_agents(board, n, tk):
    d = Path(tk.agents_dir(str(board)))
    d.mkdir(parents=True, exist_ok=True)
    now = _stamp()
    for i in range(n):
        rec = {
            "owner": "seat-%02d" % i,
            "seen": now,
            "cwd": str(board),
            "worktree": str(board),
            "branch": "lane-%02d" % i,
            "sha": "deadbeef",
        }
        (d / ("seat-%02d.json" % i)).write_text(json.dumps(rec))


def count_parses(tk, monkeypatch):
    calls = {"n": 0}
    orig = tk._parse_watch_table

    def counted():
        calls["n"] += 1
        return orig()

    monkeypatch.setattr(tk, "_parse_watch_table", counted)
    return calls


def test_nested_shared_watch_table_parses_once(tk, monkeypatch):
    calls = count_parses(tk, monkeypatch)
    with tk._shared_watch_table():
        with tk._shared_watch_table():
            tk._live_watch_pids("nobody")
            tk._watcher_count("seat-00")
    assert calls["n"] == 1


def test_who_parses_process_table_once_for_many_agents(tk, board, monkeypatch):
    n = 20
    write_agents(board, n, tk)
    calls = count_parses(tk, monkeypatch)
    ns = argparse.Namespace(no_liveness=False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        tk.cmd_who(ns, str(board))
    out = buf.getvalue()
    assert calls["n"] == 1, "who scanned ps %d times for %d seats" % (calls["n"], n)
    assert "seat-00" in out and "seat-19" in out


def test_who_no_liveness_does_not_parse_process_table(tk, board, monkeypatch):
    write_agents(board, 8, tk)
    calls = count_parses(tk, monkeypatch)
    ns = argparse.Namespace(no_liveness=True)
    with redirect_stdout(io.StringIO()):
        tk.cmd_who(ns, str(board))
    assert calls["n"] == 0


def test_health_parses_process_table_once(tk, board, monkeypatch):
    write_agents(board, 20, tk)
    tickets = tk.load_all(str(board))
    calls = count_parses(tk, monkeypatch)
    tk.health(str(board), tickets)
    assert calls["n"] == 1, "health scanned ps %d times" % calls["n"]


def test_spawn_list_parses_process_table_once(tk, board, monkeypatch):
    write_agents(board, 20, tk)
    calls = count_parses(tk, monkeypatch)
    ns = argparse.Namespace(list=True)
    buf = io.StringIO()
    with redirect_stdout(buf):
        tk.cmd_spawn(ns, str(board))
    assert calls["n"] == 1, "spawn --list scanned ps %d times" % calls["n"]
    assert "seat-00" in buf.getvalue()
