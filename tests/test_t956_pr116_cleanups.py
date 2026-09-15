"""T-956: post-merge cleanups from Atman PR #116 (advitiyavashist/atman)."""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TICKETS = os.path.join(ROOT, "tickets.py")
sys.path.insert(0, os.path.join(ROOT, "src"))
from ticket_board.session_boundary import without_board_hooks  # noqa: E402


def run(board, args, session=None, agent=None):
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
    e["TICKETS_DIR"] = os.path.join(board, ".tickets")
    return subprocess.run(
        [sys.executable, TICKETS] + args,
        cwd=board, env=e, capture_output=True, text=True,
    )


def test_note_uses_session_seat_not_ambient_ticket_agent(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    run(str(repo), ["init"], session="setup", agent="setup")
    run(str(repo), ["create", "a note"], session="setup", agent="setup")
    run(str(repo), ["join", "alice", "--roles", "backend"],
        session="alice-sid", agent="alice")
    r = run(str(repo), ["note", "T-001", "session wrote this"],
            session="alice-sid", agent="stale-ambient")
    assert r.returncode == 0, r.stderr
    t = json.loads((repo / ".tickets" / "T-001.json").read_text())
    assert t["notes"][-1]["by"] == "alice"


def test_without_board_hooks_scrubs_non_rewritten_events():
    """T-956 (3): wide scrub -- PostToolUse board hooks are stripped too."""
    settings = {
        "hooks": {
            "PostToolUse": [{"hooks": [
                {"type": "command",
                 "command": "env TICKET_AGENT=parent tickets hook-run --agent parent --event inbox"},
                {"type": "command", "command": "printf user-hook"},
            ]}],
            "UserPromptSubmit": [{"hooks": [
                {"type": "command", "command": "printf keep-me"},
            ]}],
        }
    }
    clean, changed = without_board_hooks(settings)
    assert changed is True
    post = clean["hooks"]["PostToolUse"][0]["hooks"]
    assert [c["command"] for c in post] == ["printf user-hook"]
    keep = clean["hooks"]["UserPromptSubmit"][0]["hooks"]
    assert [c["command"] for c in keep] == ["printf keep-me"]
