"""T-604 bounce: one process-table scan per snapshot, warm /board.json <1s, no pile-up."""
from __future__ import annotations

import argparse
import importlib.util
import json
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
LIVE_BOARD = Path("/Users/kavana/Downloads/steer/.tickets")


def _load_tk():
    spec = importlib.util.spec_from_file_location("tickets_t604_perf", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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


@pytest.mark.skipif(not LIVE_BOARD.is_dir(), reason="live Steer board not present")
def test_live_board_json_warm_under_1s_no_pileup():
    tk = _load_tk()
    board = str(LIVE_BOARD)
    tk.board_snapshot(board)
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        snap = tk.board_snapshot(board)
        times.append(time.perf_counter() - t0)
        assert "master" in snap and "messages" in snap and "generated" in snap
    assert all(t < 1.0 for t in times), "warm board_snapshot: %s" % times

    port = _free_port()
    ns = argparse.Namespace(json=False, host="127.0.0.1", port=port, open=False)
    threading.Thread(target=tk.cmd_ui, args=(ns, board), daemon=True).start()
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
    assert all(t < 1.0 for t in http_times), "sequential warm /board.json: %s" % http_times

    def one():
        t0 = time.perf_counter()
        with urllib.request.urlopen(url, timeout=8) as r:
            json.loads(r.read())
        return time.perf_counter() - t0

    t_overlap = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as pool:
        overlap_times = [f.result() for f in as_completed([pool.submit(one) for _ in range(3)])]
    wall = time.perf_counter() - t_overlap
    assert wall < 3.0, "overlapping /board.json wall=%.3fs times=%s" % (wall, overlap_times)
    assert all(t < 2.5 for t in overlap_times), overlap_times
