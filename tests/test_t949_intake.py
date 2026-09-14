"""T-949: intake adapters create capture-lane tickets. Never dispatch.

Throwaway boards only. Fixtures — no live GitHub or CI.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"


def clean_env(tmp_path, **overrides):
    e = {
        "PATH": str(tmp_path / "bin") + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
        "HOME": str(tmp_path / "fake-home"),
        "TICKET_AGENT": "boss",
        "PYTEST_CURRENT_TEST": "tests/test_t949_intake.py::simulated (call)",
    }
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    (tmp_path / "bin").mkdir(exist_ok=True)
    e.update(overrides)
    return e


def run(cwd, *args, env=None, tmp_path=None, stdin=None):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, cwd=str(cwd),
        env=env or clean_env(tmp_path),
        input=stdin)


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t949@test", "-c", "user.name=t949", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("t949\n")
    git(path, "add", "README")
    git(path, "commit", "-qm", "init")
    return path


def boot(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    r = run(repo, "init", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    r = run(repo, "join", "boss", "--roles", "backend", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    r = run(repo, "join", "worker-a", "--roles", "backend", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    return repo, env


def load_ticket(repo, tid):
    return json.loads((repo / ".tickets" / ("%s.json" % tid)).read_text())


def task_posts(repo):
    path = repo / ".tickets" / "messages.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("kind") == "task" or rec.get("task"):
            out.append(rec)
    return out


def test_intake_help_lists_sources():
    r = subprocess.run(
        [sys.executable, str(TOOL), "intake", "--help"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout
    blob = r.stdout.lower()
    assert "github-issue" in blob
    assert "ci-failure" in blob or "ci" in blob
    assert "cron" in blob
    assert "never" in blob or "capture" in blob
    top = subprocess.run(
        [sys.executable, str(TOOL), "--help"],
        capture_output=True, text=True)
    assert top.returncode == 0, top.stderr
    assert "intake" in top.stdout


def test_each_source_creates_capture_ticket_and_dedupes(tmp_path):
    repo, env = boot(tmp_path)

    issue = run(
        repo, "intake", "github-issue",
        "--repo", "acme/app", "--number", "42",
        "--title", "Login 500s",
        "--url", "https://github.com/acme/app/issues/42",
        env=env, tmp_path=tmp_path)
    assert issue.returncode == 0, issue.stderr + issue.stdout
    assert "created" in issue.stdout
    assert "lane=capture" in issue.stdout
    assert "gh:acme/app#42" in issue.stdout
    tid_issue = [w for w in issue.stdout.split() if w.startswith("T-")][0]

    again = run(
        repo, "intake", "issue",
        "--repo", "acme/app", "--number", "42",
        "--title", "Login 500s (dup)",
        env=env, tmp_path=tmp_path)
    assert again.returncode == 0, again.stderr + again.stdout
    assert "deduped" in again.stdout
    assert tid_issue in again.stdout

    ci = run(
        repo, "intake", "ci",
        "--repo", "acme/app", "--run-id", "987654",
        "--name", "pytest",
        "--url", "https://github.com/acme/app/actions/runs/987654",
        "--sha", "abc1234",
        env=env, tmp_path=tmp_path)
    assert ci.returncode == 0, ci.stderr + ci.stdout
    assert "created" in ci.stdout
    assert "lane=capture" in ci.stdout
    assert "ci:acme/app/run/987654" in ci.stdout
    tid_ci = [w for w in ci.stdout.split() if w.startswith("T-")][0]
    assert tid_ci != tid_issue

    ci_dup = run(
        repo, "intake", "ci-failure",
        "--repo", "acme/app", "--run-id", "987654",
        env=env, tmp_path=tmp_path)
    assert ci_dup.returncode == 0, ci_dup.stderr + ci_dup.stdout
    assert "deduped" in ci_dup.stdout
    assert tid_ci in ci_dup.stdout

    cron = run(
        repo, "intake", "cron",
        "--name", "nightly-review",
        "--schedule", "0 3 * * *",
        "--prompt", "Review yesterday's failed CI",
        env=env, tmp_path=tmp_path)
    assert cron.returncode == 0, cron.stderr + cron.stdout
    assert "created" in cron.stdout
    assert "lane=capture" in cron.stdout
    assert "cron:nightly-review@0 3 * * *" in cron.stdout
    tid_cron = [w for w in cron.stdout.split() if w.startswith("T-")][0]

    t_issue = load_ticket(repo, tid_issue)
    assert t_issue["lane"] == "capture"
    assert t_issue["intake"]["source_id"] == "gh:acme/app#42"
    assert t_issue["intake"]["url"] == "https://github.com/acme/app/issues/42"
    assert "not yet sounded" in (t_issue.get("open_questions") or [])
    assert "Do not dispatch" in (t_issue.get("body") or "")

    t_ci = load_ticket(repo, tid_ci)
    assert t_ci["lane"] == "capture"
    assert t_ci["intake"]["source_id"] == "ci:acme/app/run/987654"
    assert t_ci["intake"]["evidence"]["sha"] == "abc1234"

    t_cron = load_ticket(repo, tid_cron)
    assert t_cron["lane"] == "capture"
    assert t_cron["intake"]["source_id"] == "cron:nightly-review@0 3 * * *"

    nxt = run(repo, "next", "--owner", "worker-a", env=env, tmp_path=tmp_path)
    assert nxt.returncode != 0
    blob = (nxt.stdout + nxt.stderr).lower()
    assert tid_issue not in nxt.stdout or "sound" in blob
    assert "no ticket" in blob or "sound" in blob
    for tid in (tid_issue, tid_ci, tid_cron):
        held = load_ticket(repo, tid)
        assert held.get("status") in ("open", None) or held.get("owner") in ("", None)
        assert held.get("status") != "claimed"

    assert task_posts(repo) == []


def test_intake_json_stdin_and_missing_source_id(tmp_path):
    repo, env = boot(tmp_path)
    payload = json.dumps({
        "source": "github-issue",
        "repo": "acme/app",
        "number": 7,
        "title": "From JSON",
        "url": "https://github.com/acme/app/issues/7",
    })
    r = run(repo, "intake", "--json", "-", env=env, tmp_path=tmp_path, stdin=payload)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "created" in r.stdout
    assert "gh:acme/app#7" in r.stdout

    bad = run(repo, "intake", "github-issue", "--title", "no id",
              env=env, tmp_path=tmp_path)
    assert bad.returncode != 0
    assert "source id" in (bad.stderr + bad.stdout).lower()
    assert task_posts(repo) == []
