"""T-820: a live message poke cannot kill a persistent local watcher."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _run(board: Path, *args: str, agent: str = "sender") -> subprocess.CompletedProcess:
    env = dict(
        os.environ,
        TICKETS_DIR=str(board),
        TICKET_AGENT=agent,
        HOME=str(board.parent.parent / "home"),
    )
    env.pop("TICKETS_STOP_HOOK", None)
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=str(board.parent),
        env=env,
        capture_output=True,
        text=True,
    )


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    assert predicate()


def _count(path: Path) -> int:
    try:
        return int(path.read_text())
    except (OSError, ValueError):
        return 0


@pytest.fixture
def board(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
        env=dict(
            os.environ,
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@t",
        ),
    )
    result = _run(repo / ".tickets", "create", "Unrelated docs", "--role", "docs")
    assert result.returncode == 0, result.stderr
    joined = _run(
        repo / ".tickets",
        "join",
        "persistent-seat",
        "--roles",
        "backend",
        "--harness",
        "custom",
        "--cmd",
        "true",
        "--wake-mode",
        "continuous",
        agent="persistent-seat",
    )
    assert joined.returncode == 0, joined.stderr
    return repo / ".tickets"


def test_active_and_idle_pokes_coalesce_and_sigterm_leaves_no_orphans(
    board: Path, tmp_path: Path
) -> None:
    """One live contract covers the production crash and its lifecycle edges.

    The second message lands while run one is sleeping. Its SIGUSR1 plus five
    duplicate pokes must defer one scan until the active child exits. The third
    message lands during the five-second idle wait and must start immediately.
    SIGTERM during that third child must clean both the child and watcher lease.
    """
    count = tmp_path / "runs"
    started = tmp_path / "started"
    child_pid = tmp_path / "child.pid"
    harness = tmp_path / "harness.py"
    harness.write_text(
        "import os, pathlib, subprocess, sys, time\n"
        "count, started, child_pid, tool = map(pathlib.Path, sys.argv[1:])\n"
        "n = int(count.read_text()) + 1 if count.exists() else 1\n"
        "count.write_text(str(n))\n"
        "child_pid.write_text(str(os.getpid()))\n"
        "subprocess.run([sys.executable, str(tool), 'inbox'], "
        "check=True, stdout=subprocess.DEVNULL)\n"
        "started.write_text(str(n))\n"
        "time.sleep(30 if n == 3 else 0.8)\n"
    )
    command = "exec " + shlex.join(
        [sys.executable, str(harness), str(count), str(started), str(child_pid), str(TOOL)]
    )

    # Avoid the board's intentional second-resolution same-timestamp boundary.
    time.sleep(1.1)
    first = _run(board, "msg", "first task", "--to", "persistent-seat", "--task")
    assert first.returncode == 0, first.stderr
    env = dict(
        os.environ,
        TICKETS_DIR=str(board),
        TICKET_AGENT="persistent-seat",
        HOME=str(board.parent.parent / "home"),
    )
    watcher = subprocess.Popen(
        [
            sys.executable,
            str(TOOL),
            "watch",
            "--agent",
            "persistent-seat",
            "--persist",
            "--every",
            "3600",
            "--exec",
            command,
            "--cwd",
            str(board.parent),
        ],
        cwd=str(board.parent),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        pid_file = board / "agents" / "persistent-seat.watch.pid"
        _wait_until(lambda: pid_file.exists() and _count(started) == 1)

        second = _run(board, "msg", "second task", "--to", "persistent-seat", "--task")
        assert second.returncode == 0 and "watch-poked" in second.stdout, second.stdout
        for _ in range(5):
            os.kill(watcher.pid, signal.SIGUSR1)

        _wait_until(lambda: _count(started) == 2)
        _wait_until(
            lambda: _run(board, "pending", "--agent", "persistent-seat", "--json").returncode
            == 1
        )
        time.sleep(0.2)
        assert watcher.poll() is None, "active-child poke killed the persistent watcher"
        assert _count(count) == 2, "duplicate pokes started duplicate turns"

        # The watcher is now in its capped idle wait. A message must interrupt
        # that wait without a human prompt and start exactly one third turn.
        # Cross the board's second-resolution inbox cursor before posting it.
        time.sleep(1.1)
        third = _run(board, "msg", "third task", "--to", "persistent-seat", "--task")
        assert third.returncode == 0 and "watch-poked" in third.stdout, third.stdout
        idle_poke_at = time.monotonic()
        # Exercise the actual signal explicitly as well: cmd_msg's protected
        # compatibility path may intentionally leave older test shims on the
        # durable poke file rather than signal an unverified watcher binary.
        os.kill(watcher.pid, signal.SIGUSR1)
        _wait_until(lambda: _count(started) >= 3, timeout=2.0)
        assert time.monotonic() - idle_poke_at < 4.0
        assert _count(started) == 3
        third_child = int(child_pid.read_text())

        watcher.send_signal(signal.SIGTERM)
        watcher.wait(timeout=8)
        _wait_until(lambda: not pid_file.exists())
        with pytest.raises(ProcessLookupError):
            os.kill(third_child, 0)

        run_state = json.loads((board / "agents" / "persistent-seat.run").read_text())
        assert run_state["active"] is False
        assert run_state["interrupted"] is True
        events = [
            json.loads(line)
            for line in (board / "trajectories.jsonl").read_text().splitlines()
            if line.strip()
        ]
        starts = [e for e in events if e.get("kind") == "run_start" and e.get("agent") == "persistent-seat"]
        ends = [e for e in events if e.get("kind") == "run_end" and e.get("agent") == "persistent-seat"]
        assert len(starts) == len(ends) == 3
        assert len({e["run_id"] for e in starts}) == 3
        assert ends[-1].get("outcome") == "interrupted"
    finally:
        if watcher.poll() is None:
            watcher.send_signal(signal.SIGTERM)
            watcher.wait(timeout=8)
