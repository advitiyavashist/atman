"""T-879: README plan A then B — B becomes ready after A is done, without sound."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"

PLAN = json.dumps({
    "tickets": [
        {"key": "A", "title": "Task A: write hello.txt", "role": "backend"},
        {"key": "B", "title": "Task B: consume hello.txt", "deps": ["A"]},
    ]
})


def env_for(tmp_path, agent="alice"):
    e = dict(os.environ)
    e["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir(exist_ok=True)
    e["TICKET_AGENT"] = agent
    e.pop("TICKETS_DIR", None)
    return e


def run(repo, *args, tmp_path, agent="alice", stdin=None):
    e = env_for(tmp_path, agent)
    e["TICKETS_DIR"] = str(repo / ".tickets")
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=str(repo), env=e, text=True, capture_output=True, input=stdin)


def test_readme_plan_b_is_nextable_after_a_done(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                          GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    assert run(repo, "init", tmp_path=tmp_path).returncode == 0
    assert run(repo, "join", "alice", "--roles", "backend", tmp_path=tmp_path).returncode == 0
    assert run(repo, "join", "bob", "--roles", "backend", tmp_path=tmp_path).returncode == 0
    r = run(repo, "plan", tmp_path=tmp_path, stdin=PLAN)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "lane=ready" in r.stdout
    a = json.loads((repo / ".tickets" / "T-001.json").read_text())
    b = json.loads((repo / ".tickets" / "T-002.json").read_text())
    assert a["lane"] == "ready"
    assert b["lane"] == "ready"
    assert b["deps"] == ["T-001"]

    n = run(repo, "next", tmp_path=tmp_path, agent="alice")
    assert n.returncode == 0, n.stderr + n.stdout
    assert "T-001" in n.stdout
    d = run(repo, "done", "T-001", "--notes", "wrote hello.txt", "--force",
            tmp_path=tmp_path, agent="alice")
    assert d.returncode == 0, d.stderr + d.stdout

    b = json.loads((repo / ".tickets" / "T-002.json").read_text())
    assert b["lane"] == "ready"
    n2 = run(repo, "next", tmp_path=tmp_path, agent="bob")
    assert n2.returncode == 0, n2.stderr + n2.stdout
    assert "T-002" in n2.stdout
    assert "waiting on unfinished" not in (n2.stdout + n2.stderr).lower()


def test_readme_plan_snippet_has_no_sound_command():
    body = (ROOT / "README.md").read_text()
    start = body.index("## Plan dependent work")
    fence = body[start:].split("```sh", 1)[1].split("```", 1)[0]
    assert "tickets plan" in fence
    assert "tickets sound" not in fence
