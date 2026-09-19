"""T-1097: spawn warns on a too-small run timeout; the brief states foreground."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _load_tk():
    spec = importlib.util.spec_from_file_location("tickets_t1097", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_run_timeout_floor_warning_units_are_minutes():
    tk = _load_tk()
    assert tk.DEFAULT_RUN_TIMEOUT_MIN * 60 > 180
    assert tk.run_timeout_floor_warning(tk.DEFAULT_RUN_TIMEOUT_MIN) == ""
    assert tk.run_timeout_floor_warning(0) == ""
    assert tk.run_timeout_floor_warning(tk.RUN_TIMEOUT_FLOOR_MIN) == ""
    msg = tk.run_timeout_floor_warning(1)
    assert "warning:" in msg
    assert "1 min" in msg
    assert "%s min floor" % tk.RUN_TIMEOUT_FLOOR_MIN in msg
    assert "backgrounding" in msg


def test_seat_brief_states_the_foreground_rule():
    from ticket_board import seat_brief

    text = seat_brief.compose("alice", roles="backend", harness="cursor")
    assert seat_brief.RUNS_HEAD in text
    assert "FOREGROUND" in text
    assert "Backgrounding a test suite loses it" in text
    assert seat_brief.RUN_ONE_LINER.startswith("RUN:")
    assert "FOREGROUND" in seat_brief.RUN_ONE_LINER


def test_worker_prompt_carries_the_run_rule_every_turn(board):
    tk = _load_tk()
    from ticket_board import seat_brief

    run(board, "join", "alice", "--roles", "backend", agent="alice")
    first = run(board, "prompt", "--agent", "alice", agent="alice").stdout

    class Args:
        agent = "alice"
        master = False
        cos = False
        extra = ""
        run_no = 7

    later = tk.prompt_text(Args(), str(board))
    assert seat_brief.RUNS_HEAD in first
    assert "FOREGROUND" in first
    assert seat_brief.BRIEF_HEAD not in later
    assert seat_brief.RUN_ONE_LINER in later
    assert seat_brief.GATE_ONE_LINER in later
    assert tk.DEFAULT_RUN_TIMEOUT_MIN == 90


def test_spawn_warns_below_floor_and_prints_minutes(board):
    run(board, "join", "doc", "--roles", "docs")
    low = run(board, "spawn", "doc", "--exec", "true", "--every", "5",
              "--max-runs", "1", "--run-timeout", "1", agent="master")
    assert low.returncode == 0, low.stderr
    assert "warning: --run-timeout 1 min is below the 10 min floor" in low.stdout
    assert "run-timeout=1m" in low.stdout
    run(board, "spawn", "doc", "--stop", agent="master")
    ok = run(board, "spawn", "doc", "--exec", "true", "--every", "5",
             "--max-runs", "1", "--replace", agent="master")
    assert ok.returncode == 0, ok.stderr
    assert "warning: --run-timeout" not in ok.stdout
    assert "run-timeout=90m" in ok.stdout
    run(board, "spawn", "doc", "--stop", agent="master")
