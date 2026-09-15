"""T-880: README review without --pr works on local boards; --force skips the PR gate."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"


def run(repo, *args, tmp_path, agent="alice"):
    e = dict(os.environ, TICKETS_DIR=str(repo / ".tickets"), TICKET_AGENT=agent,
             HOME=str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    e.pop("TICKETS_STOP_HOOK", None)
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=str(repo), env=e, text=True, capture_output=True)


def _init(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
        check=True, env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    assert run(repo, "init", tmp_path=tmp_path).returncode == 0
    assert run(repo, "join", "alice", "--roles", "backend", tmp_path=tmp_path).returncode == 0
    r = run(repo, "create", "write hello.txt", "--role", "backend", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    rec = json.loads((repo / ".tickets" / "T-001.json").read_text())
    rec["sounded_at"] = "2026-09-13T00:00:00Z"
    rec["sounded_by"] = "boss"
    rec["lane"] = "ready"
    (repo / ".tickets" / "T-001.json").write_text(json.dumps(rec, indent=2))
    n = run(repo, "next", tmp_path=tmp_path)
    assert n.returncode == 0, n.stderr + n.stdout
    assert "T-001" in n.stdout
    return repo


def test_local_sounded_review_without_pr(tmp_path):
    repo = _init(tmp_path)
    r = run(repo, "review", "T-001", "--notes", "hello.txt done", "--force", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "IN REVIEW" in r.stdout
    rec = json.loads((repo / ".tickets" / "T-001.json").read_text())
    assert rec["status"] == "review"


def test_force_skips_github_pr_gate(tmp_path):
    repo = _init(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin",
         "https://github.com/example/atman.git"],
        check=True)
    r = run(repo, "review", "T-001", "--notes", "hello.txt done", tmp_path=tmp_path)
    assert r.returncode != 0
    assert "--pr" in (r.stderr + r.stdout)
    r = run(repo, "review", "T-001", "--notes", "hello.txt done", "--force", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "IN REVIEW" in r.stdout
