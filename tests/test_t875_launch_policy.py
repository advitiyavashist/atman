"""T-875 add (11): watch and spawn share one launch policy.

Manual `tickets watch` used to default to --permission-mode acceptEdits while
`tickets spawn` ran unattended (bypassPermissions). A seat started via watch
could not git commit without a prompt (T-900). Throwaway boards only.
"""
from __future__ import annotations

import importlib.util
import time

from test_wakeup import TOOL, board, ready_agent_env, run  # noqa: F401


def _tickets():
    spec = importlib.util.spec_from_file_location("tickets_t875_policy", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resolve_launch_policy_defaults_unattended():
    tk = _tickets()
    assert tk.resolve_launch_policy() == ("bypassPermissions", "unattended")
    assert tk.resolve_launch_policy(safe=True) == ("acceptEdits", "safe")
    assert tk.resolve_launch_policy(permission_mode="plan") == ("plan", "plan")
    assert tk.resolve_launch_policy(safe=True, permission_mode="bypassPermissions") == (
        "bypassPermissions", "unattended")


def test_watch_default_is_unattended_like_spawn(board):
    env = ready_agent_env(board)
    run(board, "join", "doc", "--roles", "docs")
    r = run(board, "watch", "--agent", "doc", "--once", "--dry-run", env=env)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "launch=unattended" in out, out
    assert "--dangerously-skip-permissions" in out, out
    assert "--permission-mode acceptEdits" not in out, out


def test_watch_safe_matches_spawn_safe(board):
    env = ready_agent_env(board)
    run(board, "join", "doc", "--roles", "docs")
    r = run(board, "watch", "--agent", "doc", "--once", "--dry-run", "--safe", env=env)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "launch=safe" in out, out
    assert "--permission-mode acceptEdits" in out, out
    assert "--dangerously-skip-permissions" not in out, out


def test_watch_help_names_shared_policy(board):
    r = run(board, "watch", "--help")
    text = r.stdout + r.stderr
    assert r.returncode == 0, r.stderr
    assert "--safe" in text
    assert "unattended" in text
    assert "spawn --safe" in text


def _wait_watch_log(board, owner, needle, timeout=8.0):
    log = board / "agents" / ("%s.watch.log" % owner)
    deadline = time.time() + timeout
    text = ""
    while time.time() < deadline:
        if log.exists():
            text = log.read_text()
            if needle in text:
                return text
        time.sleep(0.05)
    return text


def test_spawn_and_watch_child_print_same_unattended_policy(board):
    run(board, "join", "doc", "--roles", "docs")
    r = run(board, "spawn", "doc", "--exec", "true", "--every", "5", "--persist",
            agent="master")
    try:
        assert r.returncode == 0, r.stderr + r.stdout
        assert "launch=unattended" in r.stdout, r.stdout
        text = _wait_watch_log(board, "doc", "launch=unattended")
        assert "launch=unattended" in text, text
    finally:
        run(board, "spawn", "doc", "--stop", agent="master")


def test_spawn_safe_prints_safe_and_reaches_watch_header(board):
    run(board, "join", "doc", "--roles", "docs")
    r = run(board, "spawn", "doc", "--safe", "--exec", "true", "--every", "5",
            "--persist", agent="master")
    try:
        assert r.returncode == 0, r.stderr + r.stdout
        assert "launch=safe" in r.stdout, r.stdout
        text = _wait_watch_log(board, "doc", "launch=safe")
        assert "launch=safe" in text, text
    finally:
        run(board, "spawn", "doc", "--stop", agent="master")
