"""T-1080: atm --version names the running source and warns when it is behind.

An operator worktree that is not fast-forwarded after merge silently omits
new commands from `atm --help`. The version line must name the file and sha
that are executing, and must say so when origin/main is ahead.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
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
    tool = load_tickets()
    r = subprocess.run(
        [sys.executable, str(ROOT / "tickets.py"), "--version"],
        capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    lines = r.stdout.strip().splitlines()
    assert lines[0] == tool.PACKAGE_VERSION
    assert lines[1]
    assert any(ln.startswith("source: ") and str((ROOT / "tickets.py").resolve()) in ln
               for ln in lines)
    assert any(ln.startswith("source-sha: ") and len(ln.split()[1]) >= 7
               for ln in lines)


def test_package_version_matches_pyproject():
    tool = load_tickets()
    text = (ROOT / "pyproject.toml").read_text()
    m = None
    for line in text.splitlines():
        if line.startswith("version = "):
            m = line.split("=", 1)[1].strip().strip('"')
            break
    assert m == tool.PACKAGE_VERSION



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


def _stage_release(dest: Path, commit: str) -> Path:
    """install_live --live layout: dest/tickets.py + dest/release.json."""
    dest.mkdir(parents=True)
    src = ROOT / "tickets.py"
    data = src.read_bytes()
    (dest / "tickets.py").write_bytes(data)
    (dest / "release.json").write_text(json.dumps({
        "commit": commit,
        "files": {
            "tickets.py": {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            },
        },
    }) + "\n")
    return dest / "tickets.py"


def test_release_inside_foreign_git_repo_does_not_probe_enclosing_head(tmp_path):
    """release.json commit is the running source, not the dotfiles repo HEAD.

    install_live --live ~/.claude/tools inside a git-tracked ~/.claude must
    not claim that repo is 'behind' or suggest ff-only merge of it.
    """
    repo = tmp_path / "dotfiles"
    repo.mkdir()
    _git(tmp_path, "init", str(repo))
    _git(repo, "checkout", "-b", "main")
    (repo / "readme").write_text("old\n")
    _git(repo, "add", "readme")
    _git(repo, "commit", "-m", "old")
    old = _git(repo, "rev-parse", "HEAD")
    (repo / "readme").write_text("new\n")
    _git(repo, "add", "readme")
    _git(repo, "commit", "-m", "new")
    new = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", new)
    _git(repo, "checkout", "-q", old)

    pinned = "a" * 40
    tickets_py = _stage_release(repo / "tools", pinned)
    r = subprocess.run(
        [sys.executable, str(tickets_py), "--version"],
        capture_output=True, text=True, cwd=str(repo))
    assert r.returncode == 0, r.stderr
    out = r.stdout
    lines = out.strip().splitlines()
    assert lines[0] == load_tickets().PACKAGE_VERSION
    assert lines[1] == "tickets commit %s (verified release)" % pinned
    assert any(ln.startswith("source: ") and str(tickets_py.resolve()) in ln
               for ln in lines)
    assert not any(ln.startswith("source-sha:") for ln in lines)
    assert old not in out and old[:12] not in out
    assert "WARNING:" not in out
    assert "behind origin/main" not in out
    assert "merge --ff-only" not in out
    assert "brew upgrade atman" in out
    assert "pinned release %s" % pinned[:12] in out


def test_pinned_release_without_git_prints_brew_upgrade(tmp_path):
    pinned = "b" * 40
    tickets_py = _stage_release(tmp_path / "prefix", pinned)
    r = subprocess.run(
        [sys.executable, str(tickets_py), "--version"],
        capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    out = r.stdout
    lines = out.strip().splitlines()
    assert lines[0] == load_tickets().PACKAGE_VERSION
    assert lines[1] == "tickets commit %s (verified release)" % pinned
    assert not any(ln.startswith("source-sha:") for ln in lines)
    assert "WARNING:" not in out
    assert "pinned release %s; newer releases can't be checked from here: " \
           "brew upgrade atman (or re-run install_live)" % pinned[:12] in out
