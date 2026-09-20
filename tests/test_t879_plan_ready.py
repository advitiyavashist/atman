"""T-879: plan is ready only with real cause/change/proof; else capture + hint."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"

PLACEHOLDERS = (
    "planned in atm plan",
    "finish the ticket",
    "finish the ticket; atm done with notes",
)

PLAN_WITH_FIELDS = json.dumps({
    "tickets": [
        {
            "key": "A",
            "title": "Task A: write hello.txt",
            "role": "backend",
            "cause": "B needs hello.txt on disk",
            "change": "Write hello.txt",
            "proof": "hello.txt exists",
        },
        {
            "key": "B",
            "title": "Task B: consume hello.txt",
            "deps": ["A"],
            "cause": "A produced hello.txt",
            "change": "Read and use hello.txt",
            "proof": "consumer sees hello.txt",
        },
    ]
})

PLAN_WITHOUT_FIELDS = json.dumps({
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
    e.pop("TICKET_SEAT", None)
    e.pop("TICKET_SESSION_ID", None)
    e.pop("CLAUDE_CODE_SESSION_ID", None)
    return e


def run(repo, *args, tmp_path, agent="alice", stdin=None):
    e = env_for(tmp_path, agent)
    e["TICKETS_DIR"] = str(repo / ".tickets")
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=str(repo), env=e, text=True, capture_output=True, input=stdin)


def boot(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
        env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                 GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    assert run(repo, "init", tmp_path=tmp_path).returncode == 0
    assert run(repo, "join", "alice", "--roles", "backend", tmp_path=tmp_path).returncode == 0
    assert run(repo, "join", "bob", "--roles", "backend", tmp_path=tmp_path).returncode == 0
    return repo


def _ticket_blob(repo, tid):
    return (repo / ".tickets" / ("%s.json" % tid)).read_text()


def test_plan_with_fields_is_ready_and_b_nextable_after_a(tmp_path):
    repo = boot(tmp_path)
    r = run(repo, "plan", tmp_path=tmp_path, stdin=PLAN_WITH_FIELDS)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "lane=ready" in r.stdout
    a = json.loads((repo / ".tickets" / "T-001.json").read_text())
    b = json.loads((repo / ".tickets" / "T-002.json").read_text())
    assert a["lane"] == "ready"
    assert b["lane"] == "ready"
    assert b["deps"] == ["T-001"]
    assert a["cause"] == "B needs hello.txt on disk"
    assert b["proof"] == "consumer sees hello.txt"
    for tid in ("T-001", "T-002"):
        blob = _ticket_blob(repo, tid)
        for ph in PLACEHOLDERS:
            assert ph not in blob

    n = run(repo, "next", tmp_path=tmp_path, agent="alice")
    assert n.returncode == 0, n.stderr + n.stdout
    assert "T-001" in n.stdout
    d = run(repo, "done", "T-001", "--notes", "wrote hello.txt", "--force",
            tmp_path=tmp_path, agent="alice")
    assert d.returncode == 0, d.stderr + d.stdout
    assert "blocked: T-002 -- T-001 marked done without verification" in d.stdout

    b = json.loads((repo / ".tickets" / "T-002.json").read_text())
    assert b["lane"] == "ready"
    n2 = run(repo, "next", tmp_path=tmp_path, agent="bob")
    assert n2.returncode != 0, n2.stdout + n2.stderr
    assert "T-002" not in n2.stdout
    assert "waiting on unfinished" not in (n2.stdout + n2.stderr).lower()
    # T-1031: accept-without-review_head records the event but does not
    # release the dependent. The release path is covered by T-1031.


def test_plan_without_fields_stays_capture_with_explicit_hint(tmp_path):
    repo = boot(tmp_path)
    r = run(repo, "plan", tmp_path=tmp_path, stdin=PLAN_WITHOUT_FIELDS)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "lane=capture" in r.stdout
    a = json.loads((repo / ".tickets" / "T-001.json").read_text())
    b = json.loads((repo / ".tickets" / "T-002.json").read_text())
    assert a.get("lane") == "capture"
    assert b.get("lane") == "capture"
    assert b["deps"] == ["T-001"]
    for tid in ("T-001", "T-002"):
        blob = _ticket_blob(repo, tid)
        for ph in PLACEHOLDERS:
            assert ph not in blob
        assert "cause" not in json.loads(blob) or not json.loads(blob).get("cause")

    n = run(repo, "next", tmp_path=tmp_path, agent="alice")
    assert n.returncode != 0
    out = n.stdout + n.stderr
    assert "T-001 waits in capture: run atm sound T-001" in out
    assert "waiting on unfinished" not in out.lower()

    g = run(repo, "graph", tmp_path=tmp_path)
    assert g.returncode == 0, g.stderr
    assert "T-001 waits in capture: run atm sound T-001" in g.stdout
    assert "waiting on T-001" in g.stdout

    # Sound A only so it can be claimed; B stays capture until sounded.
    notes = "cause=B needs hello.txt; change=Write hello.txt; proof=hello.txt exists; deps=none"
    s = run(repo, "sound", "T-001", "--notes", notes, tmp_path=tmp_path, agent="alice")
    assert s.returncode == 0, s.stderr + s.stdout
    n = run(repo, "next", tmp_path=tmp_path, agent="alice")
    assert n.returncode == 0, n.stderr + n.stdout
    assert "T-001" in n.stdout
    d = run(repo, "done", "T-001", "--notes", "wrote hello.txt", "--force",
            tmp_path=tmp_path, agent="alice")
    assert d.returncode == 0, d.stderr + d.stdout
    assert "blocked: T-002 -- T-001 marked done without verification" in d.stdout
    b = json.loads((repo / ".tickets" / "T-002.json").read_text())
    assert b.get("lane") == "capture"

    n2 = run(repo, "next", tmp_path=tmp_path, agent="bob")
    assert n2.returncode != 0
    out2 = n2.stdout + n2.stderr
    assert "claimed T-002" not in out2
    assert "T-002" not in n2.stdout
    assert "waiting on unfinished" not in out2.lower()

    g2 = run(repo, "graph", tmp_path=tmp_path)
    assert "lane=capture" in g2.stdout
    assert "T-002" in g2.stdout
    assert "waiting on T-001" not in g2.stdout


def test_readme_path_uses_create_deps_and_warns_that_plan_sounds():
    body = (ROOT / "README.md").read_text()
    assert "--deps T-001" in body
    assert "atm create" in body
    assert "atm plan" in body
    assert "sounds everything it writes" in body
    src = TOOL.read_text()
    for ph in PLACEHOLDERS:
        assert ph not in src
