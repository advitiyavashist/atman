"""T-944: atm accept / atm reject bind a structured verdict to the exact SHA.

Throwaway boards only. Two-copy: tickets.py and src/ticket_board/cli.py.
Prose notes that start with accept/approved are unstructured, never Accepted.
atm done is unchanged and still starts successors without a structured accept.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import work_view  # noqa: E402
TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]
OTHER_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def run(tool, board, *args, agent="", env=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    # cli.py imported as a script needs src/ on PYTHONPATH (same as T-557).
    e["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + e.get("PYTHONPATH", "")
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(tool), *args], capture_output=True, text=True,
        env=e, cwd=str(board.parent))


def git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t944@test", "-c", "user.name=t944", *args],
        cwd=str(repo), capture_output=True, text=True, check=True)


def attach_origin(repo):
    origin = repo.parent / (repo.name + ".origin.git")
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    git(repo, "remote", "add", "origin", str(origin))
    return origin


def origin_url(repo):
    return git(repo, "config", "--get", "remote.origin.url").stdout.strip()


def pr_view(pr, sha, repo_url, branch="t944-pr"):
    return {"TICKETS_PR_VIEW": json.dumps({
        str(pr): {
            "headRefOid": sha,
            "headRefName": branch,
            "headRepository": {"nameWithOwner": repo_url},
            "repo": repo_url,
        }
    })}


def claim_on_branch(tool, board, agent="alice", branch="t944-pr"):
    repo = board.parent
    ignore = repo / ".gitignore"
    if ".tickets" not in ignore.read_text() if ignore.exists() else True:
        ignore.write_text((ignore.read_text() if ignore.exists() else "") + ".tickets\n")
        git(repo, "add", ".gitignore")
        git(repo, "commit", "-qm", "ignore board")
    assert run(tool, board, "join", agent, "--roles", "docs",
               agent=agent).returncode == 0
    claimed = run(tool, board, "claim", "T-001", agent=agent)
    if claimed.returncode != 0:
        claimed = run(tool, board, "next", agent=agent)
    assert claimed.returncode == 0, claimed.stderr + claimed.stdout
    git(repo, "checkout", "-q", "-B", branch)
    return repo


def load_ticket(board, tid):
    return json.loads((board / (tid + ".json")).read_text())


def save_ticket(board, t):
    (board / (t["id"] + ".json")).write_text(json.dumps(t, indent=2))


def repo_shas(board):
    repo = board.parent
    full = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    short = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, text=True).strip()
    return full, short


def put_in_review(board, tid="T-001", owner="alice", commit=None):
    t = load_ticket(board, tid)
    full, short = repo_shas(board)
    t["status"] = "review"
    t["owner"] = owner
    t["commit"] = commit or ("t944@%s" % short)
    t["branch"] = "t944"
    t.setdefault("notes", []).append({
        "by": owner, "at": "2026-09-14T00:00:00Z",
        "text": "REVIEW: %s -- paths: x" % t["commit"],
    })
    save_ticket(board, t)
    return t, full, short


def _pure(tickets, msgs=()):
    graph = {"nodes": [{"id": t["id"]} for t in tickets], "edges": [], "roots": []}
    w = work_view.work_payload(tickets, graph, list(msgs), objective={"text": "o"})
    return w, dict((n["id"], n) for n in w["nodes"])


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_help_lists_accept_and_reject(tool):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run([sys.executable, str(tool), "--help"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "accept" in r.stdout and "reject" in r.stdout


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_author_cannot_accept_own_work(tool, board):
    _, full, _ = put_in_review(board)
    r = run(tool, board, "accept", "T-001", "--sha", full,
            "--notes", "looks good", agent="alice")
    assert r.returncode != 0
    err = r.stderr + r.stdout
    assert "author" in err and "alice" in err
    t = load_ticket(board, "T-001")
    assert not t.get("review_events")
    assert t["status"] == "review"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_sha_mismatch_is_refused(tool, board):
    put_in_review(board)
    r = run(tool, board, "accept", "T-001", "--sha", OTHER_SHA,
            "--notes", "nope", agent="reviewer")
    assert r.returncode != 0
    err = r.stderr + r.stdout
    assert "not the submitted review head" in err
    assert not load_ticket(board, "T-001").get("review_events")


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_short_sha_is_refused_for_accept(tool, board):
    _, full, short = put_in_review(board)
    r = run(tool, board, "accept", "T-001", "--sha", short,
            "--notes", "too short", agent="reviewer")
    assert r.returncode != 0
    assert "full 40-character" in (r.stderr + r.stdout)
    assert not load_ticket(board, "T-001").get("review_events")
    ok = run(tool, board, "accept", "T-001", "--sha", full,
             "--notes", "matches head", agent="reviewer")
    assert ok.returncode == 0, ok.stderr + ok.stdout
    evs = load_ticket(board, "T-001")["review_events"]
    assert evs[0]["kind"] == "accept" and evs[0]["by"] == "reviewer"
    assert evs[0]["sha"] == full
    assert load_ticket(board, "T-001")["status"] == "review"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_reject_records_structured_event(tool, board):
    _, full, short = put_in_review(board)
    r = run(tool, board, "reject", "T-001", "--sha", short,
            "--reason", "tests missing", agent="reviewer")
    assert r.returncode == 0, r.stderr + r.stdout
    evs = load_ticket(board, "T-001")["review_events"]
    assert evs[0]["kind"] == "reject" and evs[0]["reason"] == "tests missing"
    assert evs[0]["sha"] == short
    shown = run(tool, board, "show", "T-001", agent="reviewer")
    assert shown.returncode == 0
    assert "Review events:" in shown.stdout
    assert "reject" in shown.stdout


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_later_push_supersedes_older_verdict(tool, board):
    t, full, short = put_in_review(board)
    ok = run(tool, board, "accept", "T-001", "--sha", full,
             "--notes", "first pin", agent="reviewer")
    assert ok.returncode == 0, ok.stderr
    t = load_ticket(board, "T-001")
    t["commit"] = "t944@bbbbbbb"
    save_ticket(board, t)
    _, by = _pure([load_ticket(board, "T-001")])
    r = by["T-001"]["review"]
    assert r["verified"] is False
    assert "Accepted" not in r["label"]
    assert r["history"]
    assert r["history"][0]["kind"] == "ACCEPT"
    assert r["history"][0]["applies"] == "superseded"
    assert "superseded" in r["label"]


def test_ui_label_legacy_text_is_unstructured_note_never_accepted():
    t = {"id": "T-001", "title": "x", "status": "review", "owner": "alice",
         "commit": "br@abc1234", "deps": [],
         "notes": [
             {"by": "alice", "at": "2026-09-14T00:00:00Z",
              "text": "REVIEW: br@abc1234 -- paths"},
             {"by": "spoof", "at": "2026-09-14T00:01:00Z",
              "text": "accepted, ship it"},
             {"by": "spoof", "at": "2026-09-14T00:02:00Z",
              "text": "approved"},
         ]}
    _, by = _pure([t])
    r = by["T-001"]["review"]
    assert r["verified"] is False
    assert r["latest"] is None
    assert "Accepted" not in r["label"]
    assert r["label"] == (
        "Awaiting review of abc1234 · no verdict recorded · unstructured note")
    assert by["T-001"]["verdict"] == r["label"]


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_done_semantics_unchanged_without_structured_accept(tool, board):
    assert run(tool, board, "join", "alice", "--roles", "docs",
               agent="alice").returncode == 0
    created = run(tool, board, "create", "Follow-on", "--role", "docs",
                  "--deps", "T-001", agent="alice")
    assert created.returncode == 0, created.stderr
    child = load_ticket(board, "T-002")
    assert child["status"] == "open"
    r = run(tool, board, "done", "T-001", "--notes", "handoff",
            "--force", agent="alice")
    assert r.returncode == 0, r.stderr + r.stdout
    parent = load_ticket(board, "T-001")
    assert parent["status"] == "done"
    assert not parent.get("review_events")
    assert "unblocked" in r.stdout or "started" in r.stdout


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_review_pr_refuses_unpushed_sha(tool, board):
    repo = claim_on_branch(tool, board)
    attach_origin(repo)
    git(repo, "push", "-q", "-u", "origin", "HEAD")
    old = git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "unpushed.txt").write_text("local only\n")
    git(repo, "add", "unpushed.txt")
    git(repo, "commit", "-qm", "unpushed fix")
    full = git(repo, "rev-parse", "HEAD").stdout.strip()
    r = run(tool, board, "review", "T-001", "--notes", "paths", "--pr", "173",
            agent="alice", env=pr_view("173", full, origin_url(repo)))
    assert r.returncode != 0
    err = r.stderr + r.stdout
    assert "not on origin" in err
    assert full in err
    assert old in err
    assert load_ticket(board, "T-001")["status"] != "review"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_review_pr_refuses_pr_head_mismatch(tool, board):
    repo = claim_on_branch(tool, board)
    attach_origin(repo)
    (repo / "fix.txt").write_text("fix\n")
    git(repo, "add", "fix.txt")
    git(repo, "commit", "-qm", "the real fix")
    full = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "push", "-q", "-u", "origin", "HEAD")
    r = run(tool, board, "review", "T-001", "--notes", "paths", "--pr", "173",
            agent="alice", env=pr_view("173", OTHER_SHA, origin_url(repo),
                                       branch="agy-aira2-tty-work"))
    assert r.returncode != 0
    err = r.stderr + r.stdout
    assert "does not equal or contain submitted SHA" in err
    assert full in err
    assert OTHER_SHA in err
    assert load_ticket(board, "T-001")["status"] != "review"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_review_pr_refuses_wrong_repository(tool, board):
    repo = claim_on_branch(tool, board)
    attach_origin(repo)
    (repo / "fix.txt").write_text("fix\n")
    git(repo, "add", "fix.txt")
    git(repo, "commit", "-qm", "the real fix")
    full = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "push", "-q", "-u", "origin", "HEAD")
    r = run(tool, board, "review", "T-001", "--notes", "paths", "--pr", "173",
            agent="alice", env=pr_view("173", full, "advitiyavashist/steer"))
    assert r.returncode != 0
    err = r.stderr + r.stdout
    assert "wrong repository" in err
    assert full in err
    assert load_ticket(board, "T-001")["status"] != "review"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_review_pr_refuses_dirty_worktree(tool, board):
    repo = claim_on_branch(tool, board)
    attach_origin(repo)
    (repo / "fix.txt").write_text("fix\n")
    git(repo, "add", "fix.txt")
    git(repo, "commit", "-qm", "the real fix")
    full = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "push", "-q", "-u", "origin", "HEAD")
    (repo / "fix.txt").write_text("dirty\n")
    r = run(tool, board, "review", "T-001", "--notes", "paths", "--pr", "173",
            agent="alice", env=pr_view("173", full, origin_url(repo)))
    assert r.returncode != 0
    err = r.stderr + r.stdout
    assert "dirty" in err.lower() or "uncommitted" in err.lower()
    assert load_ticket(board, "T-001")["status"] != "review"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_review_pr_binds_verified_head_for_accept(tool, board):
    repo = claim_on_branch(tool, board)
    attach_origin(repo)
    (repo / "fix.txt").write_text("fix\n")
    git(repo, "add", "fix.txt")
    git(repo, "commit", "-qm", "the real fix")
    full = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "push", "-q", "-u", "origin", "HEAD")
    r = run(tool, board, "review", "T-001", "--notes", "paths, tests", "--pr", "173",
            agent="alice", env=pr_view("173", full, origin_url(repo)))
    assert r.returncode == 0, r.stderr + r.stdout
    t = load_ticket(board, "T-001")
    assert t["status"] == "review"
    assert t.get("review_head") == full
    assert t.get("pr") in ("173", 173, "173")
    ok = run(tool, board, "accept", "T-001", "--sha", full,
             "--notes", "verified head", agent="reviewer")
    assert ok.returncode == 0, ok.stderr + ok.stdout
    evs = load_ticket(board, "T-001")["review_events"]
    assert evs[0]["kind"] == "accept" and evs[0]["sha"] == full
