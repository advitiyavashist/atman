"""Guard: a test suite must not write tickets to a live board.

Root cause: board_dir() prefers $TICKETS_DIR unconditionally, and a
subprocess a test forgets to sandbox inherits that ambient value straight
through, and board_dir()'s cwd/git-based discovery never gets a say.

The guard: while PYTEST_CURRENT_TEST is set (pytest sets it for the life of
every test) a resolved board outside the system temp dir is refused loudly,
because pytest's own tmp_path/tmpdir fixtures always live under the system
temp dir. This runs the real CLI as a subprocess, same pattern as
test_wakeup.py, so it exercises exactly what a leaking test would hit.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def run(env_overrides, cwd, *args):
    e = dict(os.environ, **env_overrides)
    e["PYTEST_CURRENT_TEST"] = "tests/test_t257_board_guard.py::simulated (call)"
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                          text=True, env=e, cwd=str(cwd))


def test_refuses_a_board_outside_tmp_while_under_pytest(tmp_path):
    # A "real" board that is NOT under the system temp dir -- standing in for
    # a live board an ambient TICKETS_DIR would otherwise point at.
    # (tmp_path itself, and anything under it, IS inside the system temp
    # dir -- that's the whole point of the guard -- so this must live
    # somewhere else entirely, e.g. next to the repo checkout.)
    fake_live_root = Path(__file__).resolve().parent / ("t257-not-under-tmp-%d" % os.getpid())
    fake_live_board = fake_live_root / ".tickets"
    fake_live_board.mkdir(parents=True, exist_ok=True)
    try:
        before = set(fake_live_board.glob("T-*.json"))
        r = run({"TICKETS_DIR": str(fake_live_board), "TICKET_AGENT": "probe"},
                tmp_path, "create", "probe ticket", "--role", "backend")
        assert r.returncode != 0, r.stdout
        assert "REFUSING" in r.stderr and "PYTEST_CURRENT_TEST" in r.stderr, r.stderr
        after = set(fake_live_board.glob("T-*.json"))
        assert after == before, "the guard must refuse BEFORE any write happens"
    finally:
        for p in fake_live_board.glob("*"):
            p.unlink()
        fake_live_board.rmdir()
        fake_live_root.rmdir()


def test_allows_a_board_that_is_under_tmp_while_under_pytest(tmp_path):
    # The normal case every other test in this suite already relies on:
    # tmp_path IS under the system temp dir, so the guard must stay silent.
    board = tmp_path / ".tickets"
    board.mkdir()
    r = run({"TICKETS_DIR": str(board), "TICKET_AGENT": "probe"},
            tmp_path, "create", "a real test ticket", "--role", "backend")
    assert r.returncode == 0, r.stderr
    assert list(board.glob("T-*.json")), "a legitimate tmp-path board must still work"


def test_does_not_fire_outside_pytest():
    # Without PYTEST_CURRENT_TEST set, a real agent's ambient TICKETS_DIR
    # (pointing anywhere, tmp or not) must resolve exactly as before.
    real_root = tempfile.mkdtemp(prefix="t257-outside-pytest-")
    board = Path(real_root) / ".tickets"
    board.mkdir()
    try:
        e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="probe")
        e.pop("PYTEST_CURRENT_TEST", None)
        r = subprocess.run([sys.executable, str(TOOL), "board"], capture_output=True,
                           text=True, env=e, cwd=real_root)
        assert r.returncode == 0, r.stderr
        assert "REFUSING" not in r.stdout and "REFUSING" not in r.stderr
    finally:
        for p in board.glob("*"):
            p.unlink()
        board.rmdir()
        os.rmdir(real_root)
