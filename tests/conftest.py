from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = Path(__file__).resolve().parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from tmp_path_reaper import reap_green_basetemp  # noqa: E402
from ui_server_harness import reap_stale_ui_servers, stop_all_ui_servers  # noqa: E402
from watch_reaper import SESSION_ROOTS, reap_watchers_under  # noqa: E402


def pytest_sessionstart(session):
    reap_stale_ui_servers()


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    stop_all_ui_servers()
    reap_green_basetemp(session, exitstatus)


@pytest.fixture(autouse=True)
def _t409_reap_spawn_watch_children(tmp_path):
    """Every test's tmp_path is scanned for leftover spawn/watch children."""
    SESSION_ROOTS.append(tmp_path)
    try:
        yield
    finally:
        reap_watchers_under(tmp_path)


def pytest_keyboard_interrupt(excinfo):
    for root in list(SESSION_ROOTS):
        reap_watchers_under(root)
