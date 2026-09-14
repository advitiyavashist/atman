"""T-875 add (12): spawn must not lie when a live watch already holds the seat.

A stale manual `tickets watch` (PATH shim, not tickets.py in argv) can hold
the pidfile while spawn reports 'watcher started (pid N)'. Detection reads
the full ps command line of the pidfile PID -- mere PID existence is not
enough (recycled PIDs). After --replace, the new pidfile must belong to the
process just started. Throwaway boards only.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

from test_wakeup import TOOL, board, run, _wait_for_path  # noqa: F401


def _tickets():
    spec = importlib.util.spec_from_file_location("tickets_t875_stale", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _start_watch(board, owner, script=None, extra=None):
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=owner,
               HOME=str(board.parent.parent / "home"))
    cwd = str(board.parent)
    argv = [sys.executable, str(script or TOOL), "watch", "--agent", owner,
            "--every", "3600", "--persist", "--exec", "true", "--cwd", cwd]
    if extra:
        argv.extend(extra)
    proc = subprocess.Popen(argv, env=env, cwd=cwd, start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid_file = board / "agents" / ("%s.watch.pid" % owner)
    _wait_for_path(pid_file)
    return proc, pid_file


def _stop_spawn(board, owner):
    run(board, "spawn", owner, "--stop", agent="master")


# ---- command-line recognition (installed tickets/atm shims) -------------

def test_watch_cmd_agent_recognises_path_tickets_shim():
    tk = _tickets()
    cmd = ("/usr/bin/python3 /Users/op/.local/bin/tickets watch "
           "--agent cos --every 60 --cwd /repo")
    assert tk._watch_cmd_agent(cmd) == "cos"


def test_watch_cmd_agent_recognises_atm_shim():
    tk = _tickets()
    cmd = "/usr/bin/python3 /Users/op/.local/bin/atm watch --agent verifier --cwd /repo"
    assert tk._watch_cmd_agent(cmd) == "verifier"


def test_watch_cmd_agent_recognises_absolute_tickets_without_python():
    tk = _tickets()
    cmd = "/Users/op/.local/bin/tickets watch --agent cos --persist"
    assert tk._watch_cmd_agent(cmd) == "cos"


def test_watch_cmd_agent_rejects_grep_tickets_needle():
    tk = _tickets()
    assert tk._watch_cmd_agent("grep -n tickets watch --agent optimizer") == ""
    assert tk._watch_cmd_agent(
        "/usr/bin/python3 /Users/op/.local/bin/tickets spawn cos --stop") == ""


# ---- pidfile classification via full ps cmdline -------------------------

def test_pidfile_unrelated_live_pid_is_not_a_watcher(board):
    tk = _tickets()
    run(board, "join", "doc", "--roles", "docs")
    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        pid_file = board / "agents" / "doc.watch.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(sleeper.pid))
        pid, cmd, kind = tk._pidfile_watch_state(str(board), "doc")
        assert kind == "unrelated", (kind, cmd)
        assert pid == sleeper.pid
        assert cmd, "full ps cmdline must be read, not empty"
        assert "sleep" in cmd
        assert tk._seat_live_watchers(str(board), "doc") == []
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=5)


def test_spawn_verify_rejects_pidfile_owned_by_other_pid(board):
    tk = _tickets()
    run(board, "join", "doc", "--roles", "docs")
    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        pid_file = board / "agents" / "doc.watch.pid"
        pid_file.write_text(str(sleeper.pid))
        ok, detail, pid, cmd = tk._spawn_verify_started_watcher(
            str(board), "doc", started_pid=999999001, timeout=0.5)
        assert ok is False
        assert pid == sleeper.pid
        assert "999999001" in detail
        assert str(sleeper.pid) in detail
        assert cmd and "sleep" in cmd
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=5)


def test_process_command_reads_full_cmdline():
    tk = _tickets()
    marker = "t875-full-cmdline-marker-%s" % os.getpid()
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(90)  # %s" % marker],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        cmd = ""
        for _ in range(20):
            cmd = tk._process_command(proc.pid)
            if marker in cmd:
                break
            time.sleep(0.05)
        assert marker in cmd, cmd
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# ---- spawn refuse / replace / verify ------------------------------------

def test_spawn_refuses_live_watch_with_full_cmdline(board):
    run(board, "join", "doc", "--roles", "docs")
    proc, pid_file = _start_watch(board, "doc")
    try:
        r = run(board, "spawn", "doc", "--exec", "true", "--persist", agent="master")
        out = r.stdout + r.stderr
        assert r.returncode != 0, out
        assert "already running" in out
        assert "refuse" in out
        assert "started" not in r.stdout
        assert "watch" in out and "--agent" in out
        assert str(proc.pid) in out or pid_file.read_text().strip() in out
        assert int(pid_file.read_text().strip()) == proc.pid
    finally:
        _stop_spawn(board, "doc")
        proc.wait(timeout=10)


def test_spawn_reclaims_pidfile_held_by_unrelated_process(board):
    run(board, "join", "doc", "--roles", "docs")
    sleeper = subprocess.Popen(["sleep", "60"])
    pid_file = board / "agents" / "doc.watch.pid"
    pid_file.write_text(str(sleeper.pid))
    try:
        r = run(board, "spawn", "doc", "--exec", "true", "--every", "5",
                "--persist", agent="master")
        try:
            assert r.returncode == 0, r.stderr + r.stdout
            assert "started (pid" in r.stdout
            assert "failure:" not in r.stderr
            new_pid = int(pid_file.read_text().strip())
            assert new_pid != sleeper.pid
            tk = _tickets()
            cmd = tk._process_command(new_pid)
            assert tk._watch_cmd_agent(cmd) == "doc", cmd
            assert "watch-cmdline:" in r.stdout
        finally:
            _stop_spawn(board, "doc")
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=5)


def test_spawn_replace_verifies_new_pidfile_is_started_watcher(board, tmp_path):
    run(board, "join", "doc", "--roles", "docs")
    bindir = tmp_path / "shim-bin"
    bindir.mkdir()
    shim = bindir / "tickets"
    shim.symlink_to(TOOL)
    old, pid_file = _start_watch(board, "doc", script=shim)
    old_pid = old.pid
    try:
        r = run(board, "spawn", "doc", "--replace", "--exec", "true",
                "--every", "5", "--persist", agent="master")
        try:
            assert r.returncode == 0, r.stderr + r.stdout
            assert "replaced" in r.stdout
            assert "started (pid" in r.stdout
            new_pid = int(pid_file.read_text().strip())
            assert new_pid != old_pid
            tk = _tickets()
            cmd = tk._process_command(new_pid)
            assert tk._watch_cmd_agent(cmd) == "doc", cmd
            assert str(new_pid) in r.stdout
            assert "watch-cmdline:" in r.stdout
        finally:
            _stop_spawn(board, "doc")
    finally:
        if old.poll() is None:
            old.terminate()
            old.wait(timeout=10)


def test_spawn_replace_help_names_pidfile_and_cmdline(board):
    r = run(board, "spawn", "--help")
    text = r.stdout + r.stderr
    assert r.returncode == 0, r.stderr
    assert "--replace" in text
    assert "cmdline" in text or "command line" in text or "ps" in text
