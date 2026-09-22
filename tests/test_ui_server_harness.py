"""Harness-level proofs for T-549: killed-runner orphan + session reaper."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401
import ui_server_harness as harness

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent


def test_ui_server_isolates_inherited_session(board, monkeypatch):
    """A server launched inside a live seat must not inherit its wake routes."""
    monkeypatch.setenv("HOME", str(board.parent / "foreign-home"))
    monkeypatch.setenv("TICKETS_CACHE_DIR", str(board.parent / "foreign-cache"))
    for key in harness._SESSION_ENV:
        monkeypatch.setenv(key, "foreign-session")
    original_popen = subprocess.Popen
    launches = []

    def capture_launch(*args, **kwargs):
        if str(harness._SUPERVISOR) in args[0]:
            launches.append(kwargs["env"].copy())
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", capture_launch)
    srv = harness.UiServer(board, probe_prefix="isolated-session")
    try:
        assert len(launches) == 1
        env = launches[0]
        assert env["HOME"] == str(board.parent.parent / "home")
        assert env["TICKETS_CACHE_DIR"] == str(board.parent / "cache")
        assert env["TICKETS_DIR"] == str(board)
        assert not set(harness._SESSION_ENV).intersection(env)
        assert srv.get("/board.json")
    finally:
        srv.stop()


def _pgrep_ui() -> list[tuple[int, str]]:
    from watch_reaper import process_cmdline
    rows: list[tuple[int, str]] = []
    if os.path.isdir("/proc"):
        try:
            names = os.listdir("/proc")
        except OSError:
            names = []
        for name in names:
            if not name.isdigit():
                continue
            rest = process_cmdline(int(name))
            if "tickets.py" in rest and " ui " in rest:
                rows.append((int(name), rest))
        return rows
    env = os.environ.copy()
    env["COLUMNS"] = "65535"
    proc = subprocess.run(
        ["ps", "-axww", "-o", "pid=,args="],
        capture_output=True, text=True, env=env,
    )
    if proc.returncode != 0:
        return []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_s, _sep, rest = line.partition(" ")
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if "tickets.py" in rest and " ui " in rest:
            rows.append((pid, rest))
    return rows


def _ui_pids_for_worktree(worktree: Path) -> set[int]:
    needle = str(worktree / "tickets.py")
    return {pid for pid, cmd in _pgrep_ui() if needle in cmd}


def test_killed_runner_leaves_no_ui_orphan(tmp_path):
    """SIGTERM mid-test must not leave tickets.py ui running (planner case A)."""
    before = _ui_pids_for_worktree(ROOT)
    probe = tmp_path / "test_hold_ui.py"
    probe.write_text(textwrap.dedent("""
        import time
        from test_wakeup import board
        from ui_server_harness import make_ui_server_fixture

        ui_server = make_ui_server_fixture("orphan-probe")

        def test_hold(board, ui_server):
            time.sleep(60)
    """))
    env = dict(os.environ)
    env["PYTHONPATH"] = "%s:%s" % (ROOT / "src", TESTS)
    runner = subprocess.Popen(
        [sys.executable, "-m", "pytest", str(probe), "-q", "-p", "no:cacheprovider"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            new_ui = _ui_pids_for_worktree(ROOT) - before
            if new_ui:
                break
            if runner.poll() is not None:
                pytest.fail("pytest subprocess exited before ui started: %s" % runner.stderr.read())
            time.sleep(0.2)
        else:
            pytest.fail("tickets ui never started within 30s")

        os.kill(runner.pid, signal.SIGTERM)
        runner.wait(timeout=15)
        time.sleep(1)
        survivors = _ui_pids_for_worktree(ROOT) - before
        assert not survivors, "orphaned tickets.py ui after SIGTERM: %s" % survivors
    finally:
        if runner.poll() is None:
            runner.kill()
            runner.wait(timeout=5)
        for pid in list(_ui_pids_for_worktree(ROOT) - before):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
