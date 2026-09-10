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


def _spawn_qwen_watch(board, tmp_path, *spawn_args, env=None):
    """Start a detached spawn without registering pids (simulates interrupt)."""
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="",
             HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    if env:
        e.update(env)
    r = subprocess.run(
        [sys.executable, str(TOOL), "spawn", "qwen", *spawn_args],
        capture_output=True, text=True, env=e, cwd=str(board.parent),
    )
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
    # Stale/missing pid file is how T-475 leaks survived T-409: ps scan must
    # still find PPID-1 watchers whose --cwd lives under this fixture root.
    pid_file.unlink(missing_ok=True)
    assert watch_pids_under(tmp_path), "ps should see the --agent qwen loop under the fixture root"
    return pid


def _assert_reaper_clears_qwen(board, tmp_path, pid):
    leftover = reap_watchers_under(tmp_path)
    assert leftover == set()
    assert not pid_alive(pid)
    assert watch_pids_under(tmp_path) == set()
    tokens = {str(tmp_path), str(Path(tmp_path).resolve()), str(Path(tmp_path).resolve())[len("/private") :]
              if str(Path(tmp_path).resolve()).startswith("/private/") else str(tmp_path)}
    assert not any(
        "--agent qwen" in cmd and any(t in cmd for t in tokens)
        for cmd in _ps_commands()
    )


def test_spawn_child_does_not_survive_fixture_reaper(board, tmp_path):
    run(board, "join", "qwen", "--roles", "docs")
    pid = _spawn_qwen_watch(board, tmp_path, "--exec", "true", "--every", "3600", "--persist")
    _assert_reaper_clears_qwen(board, tmp_path, pid)


def test_byoa_spawn_stored_harness_reaped_without_pid_file(board, tmp_path):
    script = _stub(tmp_path)
    run(board, "join", "qwen", "--roles", "docs", "--harness",
        "custom:%s {prompt_file}" % script)
    pid = _spawn_qwen_watch(board, tmp_path, "--every", "3600", "--persist")
    _assert_reaper_clears_qwen(board, tmp_path, pid)


def test_byoa_spawn_tool_flag_reaped_without_pid_file(board, tmp_path):
    script = _stub(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    codex = bindir / "codex"
    # T-686 gates built-in spawn on a zero-model auth probe. Put a stub
    # Codex on PATH the same way test_byoa.test_spawn_tool_flag_still_overrides
    # does so this reaper fixture is not a live login check.
    codex.write_text("#!/bin/sh\n"
                     "if [ \"$1\" = login ] && [ \"$2\" = status ]; then echo logged in; exit 0; fi\n"
                     "echo OK; exit 0\n")
    codex.chmod(0o755)
    env = dict(PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    run(board, "join", "qwen", "--roles", "docs", "--harness",
        "custom:%s {prompt_file}" % script, env=env)
    pid = _spawn_qwen_watch(board, tmp_path, "--tool", "codex", "--every", "3600",
                            "--persist", env=env)
    _assert_reaper_clears_qwen(board, tmp_path, pid)


def test_byoa_spawn_custom_cmd_reaped_without_pid_file(board, tmp_path):
    script = _stub(tmp_path)
    pid = _spawn_qwen_watch(
        board, tmp_path, "--harness", "custom", "--cmd",
        "%s {prompt_file}" % script, "--roles", "docs", "--every", "3600", "--persist",
    )
    _assert_reaper_clears_qwen(board, tmp_path, pid)


def _stub(tmp_path):
    script = tmp_path / "stub.sh"
    script.write_text("#!/bin/sh\necho OK\n")
    script.chmod(0o755)
    return script


def _ps_commands():
    try:
        out = subprocess.run(
            ["ps", "-ax", "-o", "command="], capture_output=True, text=True, timeout=5
        )
    except OSError:
        return []
    return out.stdout.splitlines()


def test_fixture_root_tokens_cover_var_private_aliases():
    from watch_reaper import _cmd_mentions_fixture_root, _fixture_root_tokens

    root = Path("/var/folders/qr/test-fixture")
    tokens = _fixture_root_tokens(root)
    cmd = (
        "python tickets.py watch --agent qwen --every 3600 "
        "--cwd /var/folders/qr/test-fixture/repo/.worktrees/qwen --exec true"
    )
    assert _cmd_mentions_fixture_root(cmd, tokens)
    if str(root.resolve()).startswith("/private/"):
        assert str(root.resolve()) not in cmd
