"""T-509: watch loop must die on SIGTERM and on the spawn --stop file promptly.

The gate is the poll *wait*, not the loop condition. PEP 475 retries
time.sleep after a SIGTERM handler that only sets a flag, so a single
sleep(--every) left leaked `watch --every 3600` loops running for up to an
hour after kill(2) returned 0. The stop-file check lived at the top of the
loop body, so `tickets spawn --stop` printed success and the process slept on.

These tests fail on that shape: a long --every, then TERM or a stop-file
only, and the process must be gone within a few seconds. The lock file must
be unlinked on the way out (the finally block). SIGTERM during an in-flight
--exec must also exit within a few seconds (T-683 run_end pairing).
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _start_watch(board, agent="doc", every="3600", extra=None, exec_cmd="true"):
    run(board, "join", agent, "--roles", "docs")
    home = board.parent.parent / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent,
               HOME=str(home))
    env.pop("TICKETS_STOP_HOOK", None)
    argv = [sys.executable, str(TOOL), "watch", "--agent", agent, "--every", every,
            "--persist", "--exec", exec_cmd, "--cwd", str(board.parent)]
    if extra:
        argv.extend(extra)
    proc = subprocess.Popen(
        argv,
        env=env, cwd=str(board.parent),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pid_file = board / "agents" / ("%s.watch.pid" % agent)
    for _ in range(50):
        if pid_file.exists() and proc.poll() is None:
            try:
                if int(pid_file.read_text().strip() or "0") == proc.pid:
                    break
            except ValueError:
                pass
        time.sleep(0.1)
    assert proc.poll() is None, "watch exited before the test could signal it"
    # Give it time to reach the poll wait (first pending check is cheap).
    time.sleep(0.4)
    return proc, pid_file


def _reap(proc):
    if proc.poll() is None:
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=2)


def test_sigterm_exits_long_every_watch_and_releases_the_lock(board):
    proc, pid_file = _start_watch(board)
    try:
        t0 = time.time()
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
        assert time.time() - t0 < 3.0, "SIGTERM must not wait out --every"
        assert proc.returncode is not None
        for _ in range(20):
            if not pid_file.exists():
                break
            time.sleep(0.1)
        assert not pid_file.exists(), "finally must unlink the watch lock"
    finally:
        _reap(proc)


def test_stop_file_exits_long_every_watch_without_sigterm(board):
    """The file path, independently of signals: spawn --stop writes this."""
    proc, pid_file = _start_watch(board, agent="qwen")
    try:
        stop = board / "agents" / "qwen.watch.stop"
        stop.write_text("stop")
        t0 = time.time()
        proc.wait(timeout=5)
        assert time.time() - t0 < 3.0, "stop-file must not wait out --every"
        for _ in range(20):
            if not pid_file.exists():
                break
            time.sleep(0.1)
        assert not pid_file.exists(), "finally must unlink the watch lock"
    finally:
        _reap(proc)


def test_sigterm_during_inflight_exec_exits_and_releases_lock(board):
    """Child holding {prompt_file} must not pin the watcher past a few seconds."""
    run(board, "join", "runner", "--roles", "backend", "--wake-mode", "continuous")
    sent = run(board, "msg", "held work dominates", "--to", "runner", "--task",
               agent="sender")
    assert sent.returncode == 0, sent.stderr
    cmd = "%s -c 'import sys,time; open(sys.argv[1]).read(); time.sleep(60)' {prompt_file}" % (
        sys.executable,)
    proc, pid_file = _start_watch(
        board, agent="runner", every="1", exec_cmd=cmd)
    run_file = board / "agents" / "runner.run"
    try:
        deadline = time.time() + 15
        rec = {}
        while time.time() < deadline:
            if run_file.exists():
                rec = json.loads(run_file.read_text())
                if rec.get("active"):
                    break
            time.sleep(0.1)
        else:
            raise AssertionError("watch never started a child")
        t0 = time.time()
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
        assert time.time() - t0 < 5.0, "SIGTERM during --exec must not wait out the child"
        rec = json.loads(run_file.read_text())
        assert rec.get("active") is False
        assert rec.get("interrupted") is True
        assert rec.get("rc") == 143
        for _ in range(20):
            if not pid_file.exists():
                break
            time.sleep(0.1)
        assert not pid_file.exists(), "finally must unlink the watch lock"
        path = board / "trajectories.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        ends = [e for e in rows if e.get("kind") == "run_end" and e.get("agent") == "runner"]
        assert len(ends) == 1
        assert ends[0].get("exit") == 143
        assert ends[0].get("interrupted") is True
    finally:
        _reap(proc)
