"""T-1080: atm --version names the running source and warns when it is behind.

An operator worktree that is not fast-forwarded after merge silently omits
new commands from `atm --help`. The version line must name the file and sha
that are executing, and must say so when origin/main is ahead.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_tickets():
    spec = importlib.util.spec_from_file_location("tickets_t1080", ROOT / "tickets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(repo, *args):
    r = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                 GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_version_reports_running_source_path_and_sha():
    r = subprocess.run(
        [sys.executable, str(ROOT / "tickets.py"), "--version"],
        capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    lines = r.stdout.strip().splitlines()
    assert lines[0]
    assert any(ln.startswith("source: ") and str((ROOT / "tickets.py").resolve()) in ln
               for ln in lines)
    assert any(ln.startswith("source-sha: ") and len(ln.split()[1]) >= 7
               for ln in lines)


def test_behind_origin_main_warns_and_prints_refresh(tmp_path):
    repo = tmp_path / "runtime"
    repo.mkdir()
    _git(tmp_path, "init", str(repo))
    _git(repo, "checkout", "-b", "main")
    (repo / "f").write_text("old\n")
    _git(repo, "add", "f")
    _git(repo, "commit", "-m", "old")
    old = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", old)
    (repo / "f").write_text("new\n")
    _git(repo, "add", "f")
    _git(repo, "commit", "-m", "new")
    new = _git(repo, "rev-parse", "HEAD")
    # HEAD stays at new; origin/main stays at old — then reset HEAD to old
    _git(repo, "update-ref", "refs/remotes/origin/main", new)
    _git(repo, "checkout", "-q", old)

    tool = load_tickets()
    lines = tool._source_behind_lines(str(repo), old, new)
    assert lines[0] == "WARNING: running source %s is behind origin/main %s" % (
        old[:12], new[:12])
    assert lines[1] == (
        "refresh: git -C %s fetch origin && git -C %s merge --ff-only origin/main"
        % (repo, repo))
    assert tool._source_behind_lines(str(repo), new, new) == []
