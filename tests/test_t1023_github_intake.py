"""T-1023: GitHub Issues intake -- import as lane=capture tickets, push
status back as issue comments/labels, refuse to mutate issues we did not
create without explicit config.

`gh` is never actually shelled out to in these tests: TICKETS_GH_ISSUES
injects fake `gh issue list/view` output (mirrors sounding.py's
TICKETS_PR_STATE) and TICKETS_GH_CALLS_LOG makes mutating calls (comment,
label) append to a log file instead of running `gh`.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
CLI = ROOT / "src" / "ticket_board" / "cli.py"


def clean_env(tmp_path, **overrides):
    e = {
        "PATH": str(tmp_path / "bin") + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
        "HOME": str(tmp_path / "fake-home"),
        "PYTEST_CURRENT_TEST": "tests/test_t1023_github_intake.py::simulated (call)",
    }
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    (tmp_path / "bin").mkdir(exist_ok=True)
    e.update(overrides)
    return e


def run(tool, cwd, *args, env=None, tmp_path=None):
    return subprocess.run(
        [sys.executable, str(tool), *args],
        capture_output=True, text=True, cwd=str(cwd),
        env=env or clean_env(tmp_path))


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t1023@test", "-c", "user.name=t1023", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("t1023\n")
    git(path, "add", "README")
    git(path, "commit", "-qm", "init")
    return path


def boot(tool, tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    r = run(tool, repo, "init", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    r = run(tool, repo, "join", "boss", "--roles", "backend", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    env = dict(env)
    env["TICKET_AGENT"] = "boss"
    return repo, env


ONE_ISSUE = {"acme/widgets": [
    {"number": 7, "title": "widgets fall over on empty input", "state": "OPEN",
     "url": "https://github.com/acme/widgets/issues/7",
     "body": "Steps: pass nothing. Expected: no crash.", "labels": []},
    {"number": 8, "title": "docs typo", "state": "OPEN",
     "url": "https://github.com/acme/widgets/issues/8",
     "body": "", "labels": []},
]}


def _read_calls(log_path):
    if not os.path.exists(log_path):
        return []
    with open(log_path) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_help_lists_new_subcommands():
    for tool in (TOOL, CLI):
        r = subprocess.run([sys.executable, str(tool), "--help"],
                            capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        for name in ("github-import", "github-push", "github-link"):
            assert name in r.stdout, "%s missing from %s --help" % (name, tool)


def test_import_creates_capture_lane_tickets_and_links_back(tmp_path):
    repo, env = boot(TOOL, tmp_path)
    calls_log = str(tmp_path / "gh-calls.jsonl")
    genv = dict(env, TICKETS_GH_ISSUES=json.dumps(ONE_ISSUE), TICKETS_GH_CALLS_LOG=calls_log)

    r = run(TOOL, repo, "github-import", "--repo", "acme/widgets", env=genv, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "imported acme/widgets#7" in r.stdout
    assert "imported acme/widgets#8" in r.stdout
    assert "lane=capture" in r.stdout

    tid = r.stdout.splitlines()[0].split()[3]
    assert tid.startswith("T-")

    ticket = json.loads((repo / ".tickets" / (tid + ".json")).read_text())
    assert ticket["lane"] == "capture"
    assert ticket["github_issue"]["repo"] == "acme/widgets"
    assert ticket["github_issue"]["number"] == 7
    assert ticket["github_issue"]["origin"] == "import"
    assert "Imported from acme/widgets#7" in ticket["body"]

    # not claimable yet (capture lane), same guarantee as `tickets capture`
    r = run(TOOL, repo, "next", "--owner", "worker-a", env=dict(env), tmp_path=tmp_path)
    assert r.returncode != 0
    assert "waits in capture" in r.stdout

    calls = _read_calls(calls_log)
    link_calls = [c for c in calls if c.get("kind") == "link"]
    assert len(link_calls) == 2
    assert all(c["ticket"].startswith("T-") for c in link_calls)
    assert all(c["repo"] == "acme/widgets" for c in link_calls)
    assert {c["number"] for c in link_calls} == {7, 8}


def test_import_is_idempotent_by_issue(tmp_path):
    repo, env = boot(TOOL, tmp_path)
    genv = dict(env, TICKETS_GH_ISSUES=json.dumps(ONE_ISSUE),
                TICKETS_GH_CALLS_LOG=str(tmp_path / "gh-calls.jsonl"))
    r1 = run(TOOL, repo, "github-import", "--repo", "acme/widgets", "--issue", "7",
             env=genv, tmp_path=tmp_path)
    assert r1.returncode == 0, r1.stderr
    assert "imported acme/widgets#7" in r1.stdout

    r2 = run(TOOL, repo, "github-import", "--repo", "acme/widgets", "--issue", "7",
             env=genv, tmp_path=tmp_path)
    assert r2.returncode == 0, r2.stderr
    assert "skipped acme/widgets#7" in r2.stdout
    assert "imported" not in r2.stdout


def test_push_refused_without_import_or_explicit_link(tmp_path):
    repo, env = boot(TOOL, tmp_path)
    r = run(TOOL, repo, "create", "plain ticket, no github link", env=env, tmp_path=tmp_path)
    tid = r.stdout.split()[1]

    r = run(TOOL, repo, "github-push", tid, env=env, tmp_path=tmp_path)
    assert r.returncode != 0
    assert "no linked github issue" in (r.stderr + r.stdout)


def test_push_allowed_after_import_posts_comment_and_label(tmp_path):
    repo, env = boot(TOOL, tmp_path)
    calls_log = str(tmp_path / "gh-calls.jsonl")
    genv = dict(env, TICKETS_GH_ISSUES=json.dumps(ONE_ISSUE), TICKETS_GH_CALLS_LOG=calls_log)
    r = run(TOOL, repo, "github-import", "--repo", "acme/widgets", "--issue", "7",
            env=genv, tmp_path=tmp_path)
    tid = r.stdout.split()[3]

    r = run(TOOL, repo, "github-push", tid, "--note", "triaged", env=genv, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "pushed %s (open)" % tid in r.stdout

    calls = _read_calls(calls_log)
    status_calls = [c for c in calls if c.get("kind") == "status" and c["ticket"] == tid]
    assert len(status_calls) == 1
    assert status_calls[0]["status"] == "open"
    assert "triaged" in status_calls[0]["body"]
    label_calls = [c for c in calls if c.get("action") == "label" and c["number"] == 7]
    assert len(label_calls) == 1
    assert label_calls[0]["label"] == "atman:status-open"


def test_link_without_allow_push_still_refuses_push(tmp_path):
    repo, env = boot(TOOL, tmp_path)
    calls_log = str(tmp_path / "gh-calls.jsonl")
    genv = dict(env, TICKETS_GH_ISSUES=json.dumps(ONE_ISSUE), TICKETS_GH_CALLS_LOG=calls_log)
    r = run(TOOL, repo, "create", "pre-existing ticket", env=env, tmp_path=tmp_path)
    tid = r.stdout.split()[1]

    r = run(TOOL, repo, "github-link", tid, "--repo", "acme/widgets", "--issue", "8",
            env=genv, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "push refused: not imported by us" in r.stdout

    r = run(TOOL, repo, "github-push", tid, env=genv, tmp_path=tmp_path)
    assert r.returncode != 0
    assert "not created by this board's import" in (r.stderr + r.stdout)


def test_link_with_allow_push_permits_push(tmp_path):
    repo, env = boot(TOOL, tmp_path)
    calls_log = str(tmp_path / "gh-calls.jsonl")
    genv = dict(env, TICKETS_GH_ISSUES=json.dumps(ONE_ISSUE), TICKETS_GH_CALLS_LOG=calls_log)
    r = run(TOOL, repo, "create", "pre-existing ticket 2", env=env, tmp_path=tmp_path)
    tid = r.stdout.split()[1]

    r = run(TOOL, repo, "github-link", tid, "--repo", "acme/widgets", "--issue", "8",
            "--allow-push", env=genv, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "push allowed" in r.stdout

    r = run(TOOL, repo, "github-push", tid, env=genv, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "pushed %s (open)" % tid in r.stdout


def test_cli_py_second_entry_point_has_parity(tmp_path):
    """T-243-style: the src/ticket_board/cli.py fork must not lag tickets.py."""
    repo, env = boot(CLI, tmp_path)
    calls_log = str(tmp_path / "gh-calls.jsonl")
    genv = dict(env, TICKETS_GH_ISSUES=json.dumps(ONE_ISSUE), TICKETS_GH_CALLS_LOG=calls_log)

    r = run(CLI, repo, "github-import", "--repo", "acme/widgets", "--issue", "7",
            env=genv, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    tid = r.stdout.split()[3]

    r = run(CLI, repo, "github-push", tid, env=genv, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "pushed %s (open)" % tid in r.stdout
