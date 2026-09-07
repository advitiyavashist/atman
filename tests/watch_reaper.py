"""Kill spawn/watch children that pytest fixtures leave behind (T-409).

`tickets spawn` uses start_new_session=True, so an interrupted test reparents
the loop to PPID 1 and it outlives the fixture. Register PIDs and reap on
teardown / KeyboardInterrupt. Never scan by fleet seat name -- only by pid
file, registered pid, or a command line that mentions the fixture root.
"""
from __future__ import annotations

import atexit
import os
import signal
import subprocess
import time
from pathlib import Path

WATCH_PIDS: set[int] = set()
SESSION_ROOTS: list[Path] = []


def register_watch_pid(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    if pid > 0:
        WATCH_PIDS.add(pid)


def collect_watch_pids_from_board(board):
    agents = Path(board) / "agents"
    if not agents.is_dir():
        return
    for pid_file in agents.glob("*.watch.pid"):
        try:
            pid = int(pid_file.read_text().strip() or "0")
        except (OSError, ValueError):
            continue
        register_watch_pid(pid)


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _child_pids(pid):
    try:
        out = subprocess.run(
            ["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    return [int(x) for x in out.stdout.split() if x.strip().isdigit()]


def kill_pid_tree(pid, wait_s=1.0):
    for child in _child_pids(pid):
        kill_pid_tree(child, wait_s=0.2)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def watch_pids_under(root: Path):
    """Watch-loop pids whose command line mentions this fixture root."""
    root_s = str(Path(root).resolve())
    found = set()
    try:
        out = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return found
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        cmd = parts[1]
        if root_s not in cmd:
            continue
        if "watch" in cmd and "--agent" in cmd:
            found.add(pid)
    return found


def reap_watchers_under(root: Path):
    root = Path(root)
    pids = set(WATCH_PIDS)
    pids |= watch_pids_under(root)
    try:
        for pid_file in root.rglob("*.watch.pid"):
            try:
                pid = int(pid_file.read_text().strip() or "0")
            except (OSError, ValueError):
                continue
            if pid:
                pids.add(pid)
    except OSError:
        pass
    for pid in pids:
        kill_pid_tree(pid)
        WATCH_PIDS.discard(pid)
    leftover = watch_pids_under(root)
    for pid in leftover:
        kill_pid_tree(pid)
    return leftover


def _atexit_reap():
    for root in list(SESSION_ROOTS):
        reap_watchers_under(root)
    for pid in list(WATCH_PIDS):
        kill_pid_tree(pid)
        WATCH_PIDS.discard(pid)


atexit.register(_atexit_reap)
