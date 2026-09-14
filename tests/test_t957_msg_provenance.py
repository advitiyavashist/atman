"""T-957: every new board message records sender provenance.

Absence of `session`/`via` on older records is not evidence of forgery.
Unverified senders are labelled, never rejected.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TICKETS = os.path.join(os.path.dirname(HERE), "tickets.py")


def run(board, args, env=None, session=None, agent=None):
    e = dict(os.environ)
    for var in (
        "TICKET_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID",
        "CURSOR_SESSION_ID", "TERM_SESSION_ID", "TICKET_SEAT",
    ):
        e.pop(var, None)
    e.pop("TICKET_AGENT", None)
    e.pop("TICKETS_DIR", None)
    if session is not None:
        e["TICKET_SESSION_ID"] = session
    if agent is not None:
        e["TICKET_AGENT"] = agent
    e.update(env or {})
    e["TICKETS_DIR"] = os.path.join(board, ".tickets")
    return subprocess.run(
        [sys.executable, TICKETS] + args,
        cwd=board, env=e, capture_output=True, text=True,
    )


def load_msgs(board):
    path = os.path.join(board, ".tickets", "messages.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_new_message_records_session_and_via(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    run(str(repo), ["init"], session="setup", agent="setup")
    run(str(repo), ["join", "alice", "--roles", "backend"],
        session="alice-sid", agent="alice")
    r = run(str(repo), ["msg", "hello provenance"],
            session="alice-sid", agent="alice")
    assert r.returncode == 0, r.stderr
    rec = load_msgs(str(repo))[-1]
    assert rec["from"] == "alice"
    assert rec["session"], rec
    assert rec["via"] == "session-keyed"
    assert rec["unverified"] is False
    assert "session" in rec and "via" in rec


def test_weak_via_is_labelled_unverified_but_still_posts(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    run(str(repo), ["init"], session="setup", agent="setup")
    r = run(str(repo), ["msg", "ambient only"], agent="bob")
    assert r.returncode == 0, r.stderr
    rec = load_msgs(str(repo))[-1]
    assert rec["from"] == "bob"
    assert rec["via"] == "TICKET_AGENT"
    assert rec["unverified"] is True
    assert rec.get("session") == ""
    assert "unverified:TICKET_AGENT" in r.stdout


def test_pre_provenance_record_is_absent_not_a_finding(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("tickets_t957", TICKETS)
    tk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tk)
    legacy = {"at": "2026-09-14T00:00:00Z", "from": "ceo", "to": "",
              "re": "", "text": "old"}
    assert tk.message_provenance_state(legacy) == "absent"
    rendered = tk.fmt_msg(legacy)
    assert "unverified" not in rendered
    assert "leadership-flag" not in rendered


def test_leadership_flag_when_master_posts_without_endpoint(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    run(str(repo), ["init"], session="setup", agent="setup")
    run(str(repo), ["join", "boss", "--roles", "planning"],
        session="boss-sid", agent="boss")
    r = run(str(repo), ["master", "take"], session="boss-sid", agent="boss")
    assert r.returncode == 0, r.stderr + r.stdout
    r = run(str(repo), ["msg", "direction"], agent="boss")
    assert r.returncode == 0, r.stderr
    rec = [m for m in load_msgs(str(repo)) if m.get("text") == "direction"][-1]
    assert rec["from"] == "boss"
    assert rec.get("leadership_flag") == "no-registered-endpoint"
    assert "leadership-flag:no-registered-endpoint" in r.stdout
