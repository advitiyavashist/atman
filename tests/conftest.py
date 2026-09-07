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

from tmp_path_reaper import pytest_sessionfinish  # noqa: E402,F401
from watch_reaper import SESSION_ROOTS, reap_watchers_under  # noqa: E402


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
