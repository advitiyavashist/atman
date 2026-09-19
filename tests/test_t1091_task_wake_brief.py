"""T-1091: hook-run --event task-wake briefs once, then stops.

The no-arg remote wrapper calls hook-run without TICKETS_RUN_NO. Two
consecutive fires used to both emit SEAT BRIEF. A stamped run_no=5
already skipped the brief; the wrapper path must do the same after
the first fire.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from test_wakeup import board  # noqa: F401
from test_t1031_accept_gate import run

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import provider_usage, seat_brief  # noqa: E402


def test_brief_usage_line_omits_placeholder():
    empty = provider_usage.empty_reading("claude", status="unknown", hint="no data")
    assert provider_usage.brief_usage_line(empty) == ""
    cursor = provider_usage.empty_reading("cursor")
    assert provider_usage.brief_usage_line(cursor) == ""
    live = dict(empty, status="ok", remaining="80%")
    assert "80%" in provider_usage.brief_usage_line(live)


def test_two_task_wakes_brief_only_the_first(board):
    assert run(ROOT / "tickets.py", board, "join", "alice", "--roles", "backend",
               agent="alice").returncode == 0
    first = run(ROOT / "tickets.py", board, "hook-run", "--agent", "alice",
                "--event", "task-wake", agent="alice")
    assert first.returncode == 0, first.stderr + first.stdout
    assert first.stdout.startswith(seat_brief.BRIEF_HEAD), first.stdout[:200]
    second = run(ROOT / "tickets.py", board, "hook-run", "--agent", "alice",
                 "--event", "task-wake", agent="alice")
    assert second.returncode == 0, second.stderr + second.stdout
    assert seat_brief.BRIEF_HEAD not in second.stdout
    assert "You are alice, a worker" in second.stdout


def test_env_run_no_still_skips_the_brief(board):
    assert run(ROOT / "tickets.py", board, "join", "alice", "--roles", "backend",
               agent="alice").returncode == 0
    e = dict(os.environ, TICKETS_RUN_NO="5")
    # Direct prompt with TICKETS_RUN_NO=5 (the path the review already saw).
    r = subprocess.run(
        [sys.executable, str(ROOT / "tickets.py"), "prompt", "--agent", "alice"],
        capture_output=True, text=True,
        env=dict(e, TICKETS_DIR=str(board), TICKET_AGENT="alice",
                 HOME=str(board.parent.parent / "home"), TICKET_SESSION_ID="t1091-alice"),
        cwd=str(board.parent))
    assert r.returncode == 0, r.stderr + r.stdout
    assert seat_brief.BRIEF_HEAD not in r.stdout
