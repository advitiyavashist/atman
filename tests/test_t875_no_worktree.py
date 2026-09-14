"""T-875 add (13): spawn --no-worktree keeps isolated seats off the board repo.

T-961 (Cursor) was launched with an isolated --cwd but spawn still created
Steer .worktrees/<seat> and the agent committed there. --no-worktree refuses
git worktree add, requires an existing isolated --worktree PATH, and the
Cursor harness gets `agent --workspace PATH` (never -w/--worktree).
Throwaway boards only. Does not move PR #158 @f3f607d.
"""
from __future__ import annotations

import importlib.util
import json
import os
import time

from test_wakeup import TOOL, board, run  # noqa: F401


def _tickets():
    spec = importlib.util.spec_from_file_location("tickets_t875_no_wt", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stop(board, owner):
    run(board, "spawn", owner, "--stop", agent="master")


def test_spawn_help_names_no_worktree(board):
    r = run(board, "spawn", "--help")
    assert r.returncode == 0, r.stderr
    assert "--no-worktree" in r.stdout
    assert "--workspace" in r.stdout


def test_no_worktree_without_path_fails(board):
    r = run(board, "spawn", "reviewer", "--no-worktree", "--exec", "true",
            "--persist", agent="master")
    assert r.returncode != 0
    text = r.stderr + r.stdout
    assert "--no-worktree needs --worktree" in text
    assert not (board.parent / ".worktrees" / "reviewer").exists()


def test_no_worktree_refuses_board_worktrees_path(board, tmp_path):
    leak = board.parent / ".worktrees" / "reviewer"
    leak.mkdir(parents=True)
    r = run(board, "spawn", "reviewer", "--no-worktree", "--worktree", str(leak),
            "--exec", "true", "--persist", agent="master")
    assert r.returncode != 0
    assert "refuses a path under" in (r.stderr + r.stdout)


def test_no_worktree_uses_isolated_dir_and_does_not_create_repo_worktree(board, tmp_path):
    isolated = tmp_path / "review-seat"
    isolated.mkdir()
    r = run(board, "spawn", "reviewer", "--no-worktree", "--worktree", str(isolated),
            "--exec", "true", "--persist", "--every", "5", agent="master")
    try:
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert "workspace %s (no-worktree)" % isolated in out or (
            "workspace %s (no-worktree)" % isolated.resolve() in out)
        assert "workspace: %s" % isolated.resolve() in out or (
            "workspace: %s" % isolated in out)
        default_wt = board.parent / ".worktrees" / "reviewer"
        assert not default_wt.exists()
        rec = json.loads((board / "agents" / "reviewer.json").read_text())
        want = os.path.abspath(str(isolated))
        assert rec.get("no_worktree") is True
        assert os.path.abspath(rec.get("workspace") or "") == want
        assert os.path.abspath(rec.get("cwd") or rec.get("worktree") or "") == want
        log = board / "agents" / "reviewer.watch.log"
        deadline = time.time() + 5
        text = ""
        while time.time() < deadline:
            if log.is_file():
                text = log.read_text()
                if "workspace=" in text:
                    break
            time.sleep(0.05)
        assert "workspace=" in text
        assert str(isolated.resolve()) in text or str(isolated) in text
    finally:
        _stop(board, "reviewer")


def test_worker_cmd_cursor_gets_workspace_not_git_worktree_flag():
    tk = _tickets()
    cmd = tk._worker_cmd("/tmp/board", "reviewer", tool="cursor",
                         workspace="/tmp/isolated-review")
    assert "--workspace /tmp/isolated-review" in cmd
    assert "--trust" in cmd
    assert " -w " not in cmd
    assert "--worktree" not in cmd
    assert cmd.startswith("agent -p")


def test_worker_cmd_cursor_claude_forwards_workspace():
    tk = _tickets()
    cmd = tk._worker_cmd("/tmp/board", "reviewer", tool="cursor+claude",
                         workspace="/tmp/isolated-review")
    first = cmd.split(" || ", 1)[0]
    assert "--workspace /tmp/isolated-review" in first
    assert "--worktree" not in first
