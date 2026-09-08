"""T-550: plain `tickets route` (no --shadow/--apply/--report/--score) must
work from an exported release dir, where src/ is not already on sys.path.

cmd_route's ticket_board.scheduler import (added under T-484 for the
liveness gate, root tickets.py) had no try/except-ImportError +
sys.path.insert(src) fallback, unlike every other ticket_board import in
the file (prices, turns, _scheduler_cmd, turns report). Inside this repo
PYTHONPATH=src:. hides the gap (the T-488 trap); an exported release dir
(tickets-releases/<sha>/, no src/ on sys.path, PYTHONPATH unset) hits it on
every plain `tickets route` and `tickets route --claim`.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from test_wakeup import board  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parents[1]


def _export_release(tmp_path):
    """Copy tickets.py + src/ into a fresh dir the way an exported release
    looks -- no .git, no repo-adjacent src/ already on the default sys.path."""
    release = tmp_path / "release"
    release.mkdir()
    shutil.copy(REPO_ROOT / "tickets.py", release / "tickets.py")
    shutil.copytree(REPO_ROOT / "src", release / "src")
    return release


def _run_release(release, board_dir, cwd, *args):
    env = dict(os.environ, TICKETS_DIR=str(board_dir), TICKET_AGENT="")
    env.pop("PYTHONPATH", None)
    env.pop("TICKETS_STOP_HOOK", None)
    return subprocess.run(
        [sys.executable, str(release / "tickets.py"), *args],
        capture_output=True, text=True, env=env, cwd=str(cwd))


def test_route_from_release_dir_no_pythonpath(board, tmp_path):
    release = _export_release(tmp_path)
    r = _run_release(release, board, tmp_path, "route")
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr
    assert "ModuleNotFoundError" not in r.stderr


def test_route_shadow_from_release_dir_no_pythonpath(board, tmp_path):
    release = _export_release(tmp_path)
    r = _run_release(release, board, tmp_path, "route", "--shadow")
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr
    assert "ModuleNotFoundError" not in r.stderr
