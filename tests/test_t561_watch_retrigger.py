"""T-561: watch must not re-fire on the same unread inbox after pre-inbox exit=1."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _session_env(agent):
    """Strip whatever session-id vars this test process itself happens to be
    sitting in (e.g. CLAUDE_CODE_SESSION_ID from an outer Claude Code
    session) and give the call one keyed on the actor it names.

    Without this, every call in this file -- and the raw `watch` subprocess
    below, which sets TICKET_AGENT directly -- would share one identical
    session key, and a session's RECORDED identity (from `join`) deliberately
    outranks an explicit per-invocation TICKET_AGENT (see
    tests/test_identity_precedence.py). "join doc" followed by
    "TICKET_AGENT=planner tickets msg ... --to doc" would then post as "doc",
    the name that session joined as, not "planner" -- which is indistinguishable
    from a self-addressed message and gets refused outright. Same isolation
    fix as tests/test_wakeup.py's `run()`, for the same reason.
    """
    e = dict(os.environ)
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID", "TERM_SESSION_ID"):
        e.pop(var, None)
    e["TICKET_SESSION_ID"] = "test-session-" + (agent or "__anonymous__")
    return e


def run(board, *args, agent="", env=None, cwd=None):
    e = dict(_session_env(agent), TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    if env:
        e.update(env)
    where = cwd or (board.parent if board.parent.is_dir() else Path("/"))
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                          env=e, cwd=where)


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"], check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    b = repo / ".tickets"
    r = run(b, "create", "Write docs", "--role", "docs", cwd=repo)
    assert r.returncode == 0, r.stderr
    return b


def _run_starts(board):
    path = board / "trajectories.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines()
            if ln.strip() and json.loads(ln).get("kind") == "run_start"]


def test_watch_weekly_limit_does_not_retrigger_same_inbox(board):
    run(board, "join", "doc", "--roles", "docs")
    time.sleep(1.1)
    run(board, "msg", "planner payload", "--to", "doc", agent="planner")

    limit_msg = "You've hit your weekly limit · resets Sep 13 at 8am"
    exec_cmd = 'echo "%s" 1>&2; exit 1' % limit_msg

    proc = subprocess.Popen(
        [sys.executable, str(TOOL), "watch", "--agent", "doc", "--every", "1",
         "--exec", exec_cmd],
        env=dict(_session_env("doc"), TICKETS_DIR=str(board), TICKET_AGENT="doc",
                 HOME=str(board.parent.parent / "home")),
        cwd=str(board.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(12)
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    log = (board / "agents" / "doc.watch.log").read_text()
    assert log.count("run 1 trigger=") == 1
    assert log.count("run 2 trigger=") == 0
    assert "skip retrigger" in log or len(_run_starts(board)) == 1
    assert len(_run_starts(board)) == 1

    rec = json.loads((board / "agents" / "doc.json").read_text())
    assert rec.get("limit"), "limit must be recorded from the harness log"

    inbox = run(board, "inbox", agent="doc")
    assert "planner payload" in inbox.stdout, "mail stays unread on the board"


def test_watch_pre_inbox_exit1_skips_unchanged_trigger(board):
    run(board, "join", "doc", "--roles", "docs")
    time.sleep(1.1)
    run(board, "msg", "do work", "--to", "doc", agent="planner")

    proc = subprocess.Popen(
        [sys.executable, str(TOOL), "watch", "--agent", "doc", "--every", "1",
         "--exec", "exit 1"],
        env=dict(_session_env("doc"), TICKETS_DIR=str(board), TICKET_AGENT="doc",
                 HOME=str(board.parent.parent / "home")),
        cwd=str(board.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(12)
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    log = (board / "agents" / "doc.watch.log").read_text()
    assert log.count("run 1 trigger=") == 1
    assert log.count("run 2 trigger=") == 0
    assert "skip retrigger" in log
    assert len(_run_starts(board)) == 1
