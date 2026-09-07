"""T-409: spawn/watch children must not survive the fixture.

The historical leak is test_spawn_passes_heartbeat_to0 / BYOA spawn --every
3600 without reaching --stop. This test starts that loop, then runs the same
reaper the autouse fixture uses, and asserts zero leftover watch processes
whose command line mentions this tmp_path (PPID-1 / --agent qwen / pytest-of
shape).
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from watch_reaper import collect_watch_pids_from_board, pid_alive, reap_watchers_under, watch_pids_under

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def run(board, *args, agent="", env=None, cwd=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    if env:
        e.update(env)
    r = subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                       env=e, cwd=str(cwd or board.parent))
    if args and args[0] == "spawn" and "--stop" not in args and "--list" not in args:
        collect_watch_pids_from_board(board)
    return r


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"], check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    b = repo / ".tickets"
    r = run(b, "create", "Write docs", "--role", "docs", cwd=repo)
    assert r.returncode == 0, r.stderr
    return b


def test_spawn_child_does_not_survive_fixture_reaper(board, tmp_path):
    run(board, "join", "qwen", "--roles", "docs")
    r = run(board, "spawn", "qwen", "--exec", "true", "--every", "3600", agent="master")
    assert r.returncode == 0, r.stderr + r.stdout
    pid_file = board / "agents" / "qwen.watch.pid"
    pid = None
    for _ in range(30):
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip() or "0")
            except ValueError:
                pid = 0
            if pid and pid_alive(pid):
                break
        time.sleep(0.1)
    assert pid and pid_alive(pid), "spawn did not leave a live watcher to reap"
    assert watch_pids_under(tmp_path), "ps should see the --agent qwen loop under the fixture root"
    leftover = reap_watchers_under(tmp_path)
    assert leftover == set()
    assert not pid_alive(pid)
    assert watch_pids_under(tmp_path) == set()
    root = str(Path(tmp_path).resolve())
    assert not any(
        "--agent qwen" in cmd and root in cmd
        for cmd in _ps_commands()
    )


def _ps_commands():
    try:
        out = subprocess.run(
            ["ps", "-ax", "-o", "command="], capture_output=True, text=True, timeout=5
        )
    except OSError:
        return []
    return out.stdout.splitlines()
