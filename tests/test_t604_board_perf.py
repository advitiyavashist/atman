"""T-604 bounce: one process-table scan per snapshot, warm /board.json <1s, no pile-up.

Live-board timing is opt-in via TICKET_BOARD_REAL_BOARD (a directory path).
Default CI uses the isolated `board` fixture so no operator home is named.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"
LIVE_BOARD_ENV = "TICKET_BOARD_REAL_BOARD"


def _load_tk():
    spec = importlib.util.spec_from_file_location("tickets_t604_perf", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _opt_in_live_board():
    raw = os.environ.get(LIVE_BOARD_ENV, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def _warm_snapshot_and_http(tk, board_path, snap_limit=1.0, http_limit=1.0, overlap_wall=3.0):
    tk.board_snapshot(board_path)
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        snap = tk.board_snapshot(board_path)
        times.append(time.perf_counter() - t0)
        assert "master" in snap and "messages" in snap and "generated" in snap
    assert all(t < snap_limit for t in times), "warm board_snapshot: %s" % times

    port = _free_port()
    ns = argparse.Namespace(json=False, host="127.0.0.1", port=port, open=False)
    threading.Thread(target=tk.cmd_ui, args=(ns, board_path), daemon=True).start()
    url = "http://127.0.0.1:%d/board.json" % port
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=3)
            break
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            time.sleep(0.05)
    else:
        raise RuntimeError("tickets ui thread never served /board.json")

    urllib.request.urlopen(url, timeout=5).read()
    http_times = []
    for _ in range(3):
        t0 = time.perf_counter()
        with urllib.request.urlopen(url, timeout=5) as r:
            body = json.loads(r.read())
        http_times.append(time.perf_counter() - t0)
        assert "master" in body and "messages" in body
    assert all(t < http_limit for t in http_times), "sequential warm /board.json: %s" % http_times

    def one():
        t0 = time.perf_counter()
        with urllib.request.urlopen(url, timeout=8) as r:
            json.loads(r.read())
        return time.perf_counter() - t0

    t_overlap = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as pool:
        overlap_times = [f.result() for f in as_completed([pool.submit(one) for _ in range(3)])]
    wall = time.perf_counter() - t_overlap
    assert wall < overlap_wall, "overlapping /board.json wall=%.3fs times=%s" % (wall, overlap_times)
    assert all(t < 2.5 for t in overlap_times), overlap_times


def test_ui_has_single_flight_and_threaded_server():
    html = TOOL.read_text()
    start = html.index('UI_HTML = r"""')
    ui = html[start:html.index('"""', start + 14)]
    assert "AbortController" in ui
    assert "_loadCtl" in ui
    assert "srv = ThreadingHTTPServer" in html


def test_board_snapshot_one_ps_scan(board, monkeypatch):
    tk = _load_tk()
    run(board, "join", "alpha", agent="alpha")
    run(board, "join", "beta", agent="beta")
    calls = {"ps": 0}
    real = subprocess.run

    def wrapped(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args")
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "ps":
            calls["ps"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", wrapped)
    snap = tk.board_snapshot(str(board))
    assert "agents" in snap and "master" in snap
    assert calls["ps"] == 1


def test_fixture_board_json_warm_under_1s_no_pileup(board):
    tk = _load_tk()
    run(board, "join", "alpha", agent="alpha")
    run(board, "join", "beta", agent="beta")
    _warm_snapshot_and_http(tk, str(board))


@pytest.mark.skipif(
    _opt_in_live_board() is None,
    reason="set TICKET_BOARD_REAL_BOARD to a tickets dir to run live-board timing",
)
def test_opt_in_live_board_json_warm_under_1s_no_pileup():
    tk = _load_tk()
    _warm_snapshot_and_http(tk, str(_opt_in_live_board()))
