"""T-1132: spawned seats must stamp joined_at and hide pre-join channel mail.

Root cause: `atm spawn` runs `_preflight_seat` → `_store_auth_check` →
`checkin`, which creates `agents/<owner>.json` before `cmd_join`. The old
`first_join = not _agent_rec` gate then skipped the joined_at stamp on every
spawned seat, so `_visible_after_join` was a no-op and both
`pending_work` (messages_to_me) and the Cursor SessionStart dump replayed
days-old broadcasts.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"


def _env(board, agent=""):
    e = dict(os.environ, TICKETS_DIR=str(board), HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID",
                "TERM_SESSION_ID", "CURSOR_CONVERSATION_ID", "TICKET_SEAT",
                "TICKETS_WATCH_PINNED", "TICKETS_RUN_ID", "TICKETS_RUN_NO"):
        e.pop(var, None)
    e["TICKET_SESSION_ID"] = "test-session-" + (agent or "__anonymous__")
    e["TICKET_AGENT"] = agent or ""
    return e


def run(board, *args, agent="", entry="root"):
    e = _env(board, agent=agent)
    if entry == "pkg":
        e["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT)])
        cmd = [sys.executable, "-m", "ticket_board", *args]
    else:
        cmd = [sys.executable, str(TOOL), *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=e, cwd=board.parent)


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    b = repo / ".tickets"
    b.mkdir(parents=True)
    return b


def _history(board, n=8, entry="root"):
    run(board, "join", "master", "--roles", "backend", agent="master", entry=entry)
    for i in range(n):
        # Idle lines with a fake @sha reproduce the T-1132 mislead: parse_mentions
        # treats the hex as an @handle so is_board_broadcast is false.
        run(board, "msg",
            "idle: T-10%d IN REVIEW @deadbeef%d" % (i, i),
            agent="master", entry=entry)


def _preflight_stub(board, owner):
    """Create agents/<owner>.json the way spawn auth does, with no joined_at."""
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT))
    import tickets as tk
    tk.checkin(str(board), owner)
    rec = json.loads((board / "agents" / (owner + ".json")).read_text())
    assert "joined_at" not in rec, rec


@pytest.mark.parametrize("entry", ["root", "pkg"])
def test_spawn_preflight_then_join_stamps_joined_at(board, entry):
    _history(board, 4, entry=entry)
    time.sleep(1.1)
    _preflight_stub(board, "newbie")
    run(board, "join", "newbie", "--roles", "backend", agent="newbie", entry=entry)
    rec = json.loads((board / "agents" / "newbie.json").read_text())
    assert rec.get("joined_at"), "spawned seat must get joined_at despite preflight stub"


def test_spawned_seat_pending_excludes_prejoin_idle_broadcasts(board):
    _history(board, 6)
    time.sleep(1.1)
    _preflight_stub(board, "newbie")
    run(board, "join", "newbie", "--roles", "backend", agent="newbie")
    # Real DM after join must still surface.
    run(board, "msg", "please take T-1132", "--to", "newbie", agent="master")
    r = run(board, "pending", "--agent", "newbie", "--json")
    p = json.loads(r.stdout)
    joined = " ".join(p.get("messages_to_me") or [])
    assert "please take T-1132" in joined, r.stdout
    assert "idle: T-10" not in joined, r.stdout
    assert p.get("broadcasts") in (None, 0) or "idle: T-10" not in str(p)


def test_idle_with_unregistered_sha_is_not_messages_to_me(board):
    """Post-join idle @sha must count as channel mail, not a personal DM."""
    run(board, "join", "bob", "--roles", "backend", agent="bob")
    run(board, "msg", "idle: T-999 done @abcdef1", agent="master")
    r = run(board, "pending", "--agent", "bob", "--json")
    p = json.loads(r.stdout)
    assert "messages_to_me" not in p, p
    assert p.get("broadcasts") == 1, r.stdout


def test_cursor_session_start_dump_honors_joined_at(board, tmp_path):
    """The baked Cursor hook must not dump pre-join channel history."""
    import tickets as tk

    _history(board, 5)
    time.sleep(1.1)
    _preflight_stub(board, "hookseat")
    run(board, "join", "hookseat", "--roles", "backend", agent="hookseat")
    run(board, "msg", "AFTER join ping", "--to", "hookseat", agent="master")
    # Brief that predates join must still appear (T-327).
    joined = json.loads((board / "agents" / "hookseat.json").read_text())["joined_at"]
    # Rewrite one directed brief to before join to assert the exception.
    path = board / "messages.jsonl"
    lines = path.read_text().splitlines()
    brief = {
        "at": "2020-01-01T00:00:00Z",
        "from": "master",
        "to": "hookseat",
        "text": "your brief: work T-1132",
    }
    assert brief["at"] < joined
    path.write_text("\n".join(lines + [json.dumps(brief)]) + "\n")

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    script = hooks_dir / "tickets-board.py"
    # Render the same template `atm hooks cursor` installs.
    rendered = tk.CURSOR_HOOK % {
        "agent": "hookseat",
        "board": str(board),
        "script": str(TOOL),
    }
    script.write_text(rendered)
    script.chmod(0o755)
    env = _env(board, agent="hookseat")
    proc = subprocess.run(
        [sys.executable, str(script), "--agent", "hookseat"],
        input=json.dumps({"hook_event_name": "SessionStart"}),
        capture_output=True, text=True, env=env, cwd=board.parent)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout or "{}")
    ctx = out.get("additional_context") or ""
    assert "idle: T-10" not in ctx, ctx
    assert "your brief: work T-1132" in ctx, ctx
    assert "AFTER join ping" in ctx, ctx
