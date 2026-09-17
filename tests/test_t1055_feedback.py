"""T-1055: `atm feedback` -- a local-only, pasteable run summary.

No telemetry: the promise is that the board stays on the user's machine, so
this reads only existing board data, prints to stdout, and sends nothing
anywhere. Throwaway boards only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"


def run(board, *args, agent=""):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    e.pop("TICKET_SEAT", None)
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID",
                "TERM_SESSION_ID", "TICKET_SESSION_ID"):
        e.pop(var, None)
    if agent:
        e["TICKET_SESSION_ID"] = "test-session-" + agent
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                          env=e, cwd=str(board.parent))


def load_ticket(board, tid):
    return json.loads((board / (tid + ".json")).read_text())


def save_ticket(board, t):
    (board / (t["id"] + ".json")).write_text(json.dumps(t, indent=2) + "\n")


def test_feedback_on_a_fresh_board_prints_zeros_not_errors(board):
    """A board where nothing has succeeded yet is the most informative case,
    not an error case: `atm feedback` must not crash on it."""
    for p in board.glob("T-*.json"):
        p.unlink()
    r = run(board, "feedback")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "tickets created:          0" in out
    assert "tickets reviewed:         0" in out
    assert "tickets accepted:         0" in out
    assert "tickets done, unverified: 0" in out
    assert "release overrides:        0" in out
    assert "reopens: 0" in out
    assert "objective: not set" in out
    assert "seats LIMITED (hit a usage limit): none" in out
    assert 'seats with no live response (closest tracked state to "stalled"): none' in out
    assert "Nothing here is sent anywhere." in out


def test_feedback_counts_reviewed_accepted_and_unverified_done(board):
    sha = "a" * 40
    t1 = load_ticket(board, "T-001")
    t1["status"] = "done"
    t1["review_head"] = sha
    t1["review_events"] = [{"kind": "accept", "by": "boss", "at": "2026-09-16T00:00:00Z", "sha": sha}]
    save_ticket(board, t1)

    r = run(board, "create", "Second ticket", "--role", "docs")
    assert r.returncode == 0, r.stderr
    t2 = load_ticket(board, "T-002")
    t2["status"] = "done"  # done, but never reviewed or accepted (T-992)
    save_ticket(board, t2)

    out = run(board, "feedback").stdout
    assert "tickets created:          2" in out
    assert "tickets reviewed:         1" in out
    assert "tickets accepted:         1" in out
    assert "tickets done, unverified: 1" in out


def test_feedback_counts_reopens(board):
    t = load_ticket(board, "T-001")
    t["status"] = "claimed"
    t["owner"] = "alice"
    save_ticket(board, t)
    r = run(board, "reopen", "T-001", "--notes", "handing to someone else")
    assert r.returncode == 0, r.stderr + r.stdout
    out = run(board, "feedback").stdout
    assert "reopens: 1" in out


def test_feedback_lists_a_limited_seat(board):
    r = run(board, "limit", "alice", "--note", "quota")
    assert r.returncode == 0, r.stderr
    out = run(board, "feedback").stdout
    assert "seats LIMITED (hit a usage limit): alice" in out


def test_feedback_redacts_home_and_repo_path(board):
    home = str(board.parent.parent / "home")
    repo = str(board.parent)
    out = run(board, "feedback").stdout
    assert home not in out
    assert repo not in out
    assert "<HOME>" in out or "<RUN>" in out


def _feedback_env(home, board):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="", HOME=str(home))
    e.pop("TICKETS_STOP_HOOK", None)
    e.pop("TICKET_SEAT", None)
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID",
                "TERM_SESSION_ID", "TICKET_SESSION_ID"):
        e.pop(var, None)
    return e


def test_feedback_redacts_outside_a_git_repo(tmp_path):
    """git_state() is None outside a worktree; the checkout must still be scrubbed.

    Old cmd_feedback took run/repo_name from git_state()["top"], so a non-git
    cwd printed the full checkout (e.g. code/myrepo) and still claimed the
    text was redacted. This fails on that code.
    """
    home = tmp_path / "home"
    repo = tmp_path / "code" / "myrepo"
    board = repo / ".tickets"
    home.mkdir()
    repo.mkdir(parents=True)
    assert not (repo / ".git").exists()

    env = _feedback_env(home, board)
    created = subprocess.run(
        [sys.executable, str(TOOL), "create", "Throwaway ticket"],
        capture_output=True, text=True, env=env, cwd=str(repo))
    assert created.returncode == 0, created.stderr
    r = subprocess.run(
        [sys.executable, str(TOOL), "feedback"],
        capture_output=True, text=True, env=env, cwd=str(repo))
    assert r.returncode == 0, r.stderr
    out = r.stdout

    home_s = str(home)
    repo_s = str(repo)
    for leaked in (
        home_s, os.path.abspath(home_s), os.path.realpath(home_s),
        repo_s, os.path.abspath(repo_s), os.path.realpath(repo_s),
        str(tmp_path), os.path.realpath(tmp_path),
    ):
        assert leaked not in out, leaked
    assert "myrepo" not in out
    assert "code/myrepo" not in out
    assert "board: /" not in out
    assert not re.search(r"(?m)(^|[\s:=])/(?:Users|home|private|var|tmp|Volumes)/", out), out
    claim = "Redacted before printing: your home directory, this checkout's path, and its folder name."
    paths_remain = any(p in out for p in (home_s, repo_s, "myrepo"))
    assert (claim in out) == (not paths_remain)
    assert claim in out


def test_feedback_redaction_claim_only_when_every_path_was_scrubbed():
    """The printed claim is a promise: empty run (git_state None) is not success."""
    import tickets as tk

    leaked = "board: /Users/me/code/myrepo/.tickets"
    half = tk._feedback_redact(leaked, home="/Users/me", run="", repo_name="")
    assert "myrepo" in half
    assert not tk._feedback_redaction_ok(half, home="/Users/me", run="", repo_name="")

    clean = tk._feedback_redact(
        leaked, home="/Users/me", run="/Users/me/code/myrepo",
        repo="/Users/me/code/myrepo", repo_name="myrepo")
    assert "/Users/me" not in clean
    assert "myrepo" not in clean
    assert tk._feedback_redaction_ok(
        clean, home="/Users/me", run="/Users/me/code/myrepo",
        repo="/Users/me/code/myrepo", repo_name="myrepo")
