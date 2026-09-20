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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session_adapters import AMBIENT_TRANSPORT_VARS, TRANSPORT_BOARD_ENV  # noqa: E402
from tmp_path_reaper import reap_green_basetemp  # noqa: E402
from ui_server_harness import (  # noqa: E402
    leftover_suite_ui_children,
    reap_stale_ui_servers,
    stop_all_ui_servers,
)
from watch_reaper import SESSION_ROOTS, reap_watchers_under  # noqa: E402


def pytest_sessionstart(session):
    reap_stale_ui_servers()


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    stop_all_ui_servers()
    leftover = leftover_suite_ui_children()
    if leftover:
        session.exitstatus = 1
        print("T-960: suite left ui children alive: %s" % leftover)
    reap_green_basetemp(session, exitstatus)


@pytest.fixture(autouse=True)
def _t1107_scrub_inherited_session_transport(monkeypatch):
    """No test may inherit the transport of the session running the suite.

    CLAUDE_CODE_MESSAGING_SOCKET/TOKEN, CODEX_THREAD_ID and
    CURSOR_CONVERSATION_ID are process-global: whoever runs pytest inside a
    live Claude/Codex/Cursor session hands every test a working wake path into
    that session, and one scratch-board wake then lands in the operator's real
    window (T-1107). A guard here, not per harness, so a new test file or a
    direct in-process `session_adapters` call cannot miss it. A test that
    wants a transport sets its own (decoy) value after this runs.
    """
    for var in (*AMBIENT_TRANSPORT_VARS, TRANSPORT_BOARD_ENV):
        monkeypatch.delenv(var, raising=False)


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
