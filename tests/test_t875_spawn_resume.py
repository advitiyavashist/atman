"""T-875: spawn --stop then spawn restores seat config; location is the seat tree."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time

import pytest

from test_wakeup import TOOL, board, run  # noqa: F401


def _genv():
    return dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def _agent(board, name):
    return json.loads((board / "agents" / ("%s.json" % name)).read_text())


def test_stop_resume_restores_harness_roles_brief_persist(board):
    r = run(board, "spawn", "doc", "--harness", "custom", "--cmd", "true {prompt_file}",
            "--roles", "docs,verification", "--model", "sonnet",
            "--brief", "standing seat brief", "--persist", "--every", "5",
            "--exec", "true", agent="master")
    assert r.returncode == 0, r.stderr + r.stdout
    try:
        rec = _agent(board, "doc")
        assert rec.get("spawn", {}).get("harness") == "custom"
        assert rec["spawn"]["roles"] == "docs,verification"
        assert rec["spawn"]["model"] == "sonnet"
        assert rec["spawn"]["persist"] is True
        assert rec["spawn"]["brief"]
        assert rec["spawn"]["worktree"]
        wt = rec["spawn"]["worktree"]
    finally:
        run(board, "spawn", "doc", "--stop", agent="master")
        pid_file = board / "agents" / "doc.watch.pid"
        for _ in range(20):
            if not pid_file.exists():
                break
            time.sleep(0.25)

    wf_path = board / "workforce.json"
    wf = json.loads(wf_path.read_text())
    wf["doc"].pop("harness", None)
    wf["doc"].pop("tool", None)
    wf["doc"].pop("model", None)
    wf["doc"].pop("cmd", None)
    wf_path.write_text(json.dumps(wf, indent=2))
    roles = json.loads((board / "roles.json").read_text())
    roles["doc"] = []
    (board / "roles.json").write_text(json.dumps(roles, indent=2))
    brief = board / "briefs" / "doc.md"
    if brief.exists():
        brief.unlink()

    r2 = run(board, "spawn", "doc", agent="master")
    assert r2.returncode == 0, r2.stderr + r2.stdout
    try:
        assert "harness=custom" in r2.stdout
        assert "persist=yes" in r2.stdout
        assert "model=sonnet" in r2.stdout
        rec2 = _agent(board, "doc")
        assert rec2["spawn"]["worktree"] == wt
        assert json.loads((board / "workforce.json").read_text())["doc"]["harness"] == "custom"
        assert "docs" in json.loads((board / "roles.json").read_text())["doc"]
        assert "standing seat brief" in brief.read_text()
        lst = run(board, "spawn", "--list").stdout
        assert "doc" in lst and "pid" in lst
    finally:
        run(board, "spawn", "doc", "--stop", agent="master")


def test_spawn_records_seat_worktree_not_caller_cwd(board, tmp_path):
    caller = tmp_path / "caller-cwd"
    caller.mkdir()
    r = run(board, "spawn", "loc", "--exec", "true", "--persist", "--every", "5",
            "--roles", "docs", agent="master", cwd=caller)
    assert r.returncode == 0, r.stderr + r.stdout
    try:
        rec = _agent(board, "loc")
        seat = rec.get("worktree") or ""
        assert (board.parent / ".worktrees" / "loc").is_dir()
        assert os.path.realpath(seat) == os.path.realpath(board.parent / ".worktrees" / "loc")
        assert os.path.realpath(rec["cwd"]) == os.path.realpath(seat)
        assert str(caller) not in (rec.get("cwd") or "")
        msgs = (board / "messages.jsonl").read_text()
        assert ".worktrees/loc" in msgs
        assert str(caller) not in msgs
    finally:
        run(board, "spawn", "loc", "--stop", agent="master")


def test_spawn_worktree_uses_target_repo_not_board_repo(board, tmp_path):
    target = tmp_path / "atman-like"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, env=_genv())
    wt = target / ".worktrees" / "cross"
    r = run(board, "spawn", "cross", "--worktree", str(wt), "--base", "HEAD",
            "--exec", "true", "--persist", "--every", "5", "--roles", "docs",
            agent="master")
    assert r.returncode == 0, r.stderr + r.stdout
    try:
        assert wt.is_dir()

        def git_common(path):
            raw = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                cwd=str(path), capture_output=True, text=True, check=True).stdout.strip()
            return os.path.realpath(raw if os.path.isabs(raw) else os.path.join(str(path), raw))

        common = git_common(wt)
        board_git = git_common(board.parent)
        target_git = git_common(target)
        assert common == target_git
        assert common != board_git
        rec = _agent(board, "cross")
        assert os.path.realpath(rec["worktree"]) == os.path.realpath(wt)
    finally:
        run(board, "spawn", "cross", "--stop", agent="master")


def test_persist_watcher_death_is_reported(board):
    spec = importlib.util.spec_from_file_location("tickets_t875", TOOL)
    tk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tk)
    log_path = board / "agents" / "doc.watch.log"
    (board / "agents").mkdir(exist_ok=True)
    log_path.write_text("watching ...\nwatch stopped\n")
    with pytest.raises(SystemExit) as ei:
        tk._confirm_watcher_started(str(board), "doc", str(log_path), True)
    assert "died after start" in str(ei.value)
    assert "watch stopped" in str(ei.value)
