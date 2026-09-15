"""T-955 train-3: tickets.py must not act on LIFE_UNKNOWN reopen posts."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board, run  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TICKETS = ROOT / "tickets.py"
TOOLS = [TICKETS, ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]
SAME = "2026-09-14T12:00:00Z"
NEWER = "2026-09-14T12:00:01Z"


def _mod():
    spec = importlib.util.spec_from_file_location("tickets_t955", TICKETS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_unknown_same_second_task_does_not_wake(board):
    run(board, "join", "planner", "--roles", "leadership", agent="planner")
    run(board, "join", "carol", "--roles", "backend", agent="carol")
    run(board, "master", "take", agent="planner")
    run(board, "create", "Work", "--role", "backend", agent="planner")
    t = {"id": "T-001", "reopened_at": SAME, "reopened_seen": []}
    m = {"id": "m-same", "kind": "task", "re": "T-001", "to": "carol",
         "from": "planner", "at": SAME, "text": "take T-001", "task": True}
    # persist ticket reopen stamp without a cutoff
    path = board / "T-001.json"
    import json
    rec = json.loads(path.read_text())
    rec["reopened_at"] = SAME
    rec.pop("reopened_seen", None)
    path.write_text(json.dumps(rec, indent=2))
    tk = _mod()
    assert tk._task_life_actionable(str(board), m) is False
    assert tk._message_wakes_seat(str(board), "carol", m) is False
    assert tk._reopen_blocks_automation(str(board), rec, messages=[m]) is True


def test_cutoff_makes_same_second_task_previous_not_unknown(board):
    import json
    run(board, "join", "planner", "--roles", "leadership", agent="planner")
    run(board, "create", "Work", "--role", "backend", agent="planner")
    rec = json.loads((board / "T-001.json").read_text())
    rec["reopened_at"] = SAME
    rec["reopened_seen"] = ["m-same"]
    (board / "T-001.json").write_text(json.dumps(rec, indent=2))
    m = {"id": "m-same", "kind": "task", "re": "T-001", "to": "carol",
         "from": "planner", "at": SAME, "text": "take T-001", "task": True}
    later = {"id": "m-new", "kind": "task", "re": "T-001", "to": "carol",
             "from": "planner", "at": "2026-09-14T12:00:01Z", "text": "take now",
             "task": True}
    tk = _mod()
    assert tk._task_life_actionable(str(board), m) is False
    assert tk._task_life_actionable(str(board), later) is True
    assert tk._reopen_blocks_automation(str(board), rec, messages=[m]) is False
    assert tk._reopen_blocks_automation(str(board), rec, messages=[m, later]) is False


def test_dispatch_refuses_unknown_only_reopen_task(board):
    import json
    run(board, "join", "planner", "--roles", "leadership,planner", agent="planner")
    run(board, "master", "take", agent="planner")
    run(board, "create", "Work", "--role", "backend", agent="planner")
    rec = json.loads((board / "T-001.json").read_text())
    rec["reopened_at"] = SAME
    rec.pop("reopened_seen", None)
    (board / "T-001.json").write_text(json.dumps(rec, indent=2))
    msgs = board / "messages.jsonl"
    msgs.write_text(json.dumps({
        "id": "m-same", "kind": "task", "re": "T-001", "to": "carol",
        "from": "planner", "at": SAME, "text": "take T-001", "task": True,
    }) + "\n")
    r = run(board, "dispatch", "T-001", "--to", "carol", "--harness", "cursor",
            agent="planner")
    assert r.returncode != 0
    assert "same-second-as-reopen" in (r.stderr + r.stdout)


def _stamp_unknown_reopen(board, tid, to="carol"):
    rec = json.loads((board / ("%s.json" % tid)).read_text())
    rec["reopened_at"] = SAME
    rec.pop("reopened_seen", None)
    (board / ("%s.json" % tid)).write_text(json.dumps(rec, indent=2))
    (board / "messages.jsonl").write_text(json.dumps({
        "id": "m-same", "kind": "task", "re": tid, "to": to,
        "from": "planner", "at": SAME, "text": "take %s" % tid, "task": True,
    }) + "\n")
    return rec


def _run_tool(tool, board, *args, agent=""):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    for var in ("TICKET_SEAT", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID",
                "CURSOR_SESSION_ID", "TERM_SESSION_ID"):
        e.pop(var, None)
    actor = agent or "__anonymous__"
    e["TICKET_SESSION_ID"] = "test-session-" + actor
    return subprocess.run(
        [sys.executable, str(tool), *args], capture_output=True, text=True,
        cwd=str(board.parent), env=e)


def _combined(r):
    return (r.stdout or "") + (r.stderr or "")


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_next_refuses_unknown_only_reopen_task_root_and_package(tool, board):
    run(board, "join", "planner", "--roles", "leadership", agent="planner")
    run(board, "join", "docs-worker", "--roles", "docs", agent="docs-worker")
    _stamp_unknown_reopen(board, "T-001", to="docs-worker")

    r = _run_tool(tool, board, "next", agent="docs-worker")
    out = _combined(r)
    assert r.returncode != 0, out
    assert "Traceback" not in out, out
    assert "no ticket ready" in out, out
    rec = json.loads((board / "T-001.json").read_text())
    assert rec["status"] == "open", "LIFE_UNKNOWN reopen must not be next-claimed:\n%s" % out


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_next_claims_when_strictly_newer_message_root_and_package(tool, board):
    run(board, "join", "planner", "--roles", "leadership", agent="planner")
    run(board, "join", "docs-worker", "--roles", "docs", agent="docs-worker")
    rec = json.loads((board / "T-001.json").read_text())
    rec["reopened_at"] = SAME
    rec.pop("reopened_seen", None)
    (board / "T-001.json").write_text(json.dumps(rec, indent=2))
    (board / "messages.jsonl").write_text(json.dumps({
        "id": "m-same", "kind": "task", "re": "T-001", "to": "docs-worker",
        "from": "planner", "at": SAME, "text": "take T-001", "task": True,
    }) + "\n" + json.dumps({
        "id": "m-new", "kind": "task", "re": "T-001", "to": "docs-worker",
        "from": "planner", "at": NEWER, "text": "take now", "task": True,
    }) + "\n")

    r = _run_tool(tool, board, "next", agent="docs-worker")
    out = _combined(r)
    assert r.returncode == 0, out
    assert "Traceback" not in out, out
    rec = json.loads((board / "T-001.json").read_text())
    assert rec["status"] == "claimed", out
    assert rec.get("owner") == "docs-worker", rec


def test_unknown_reopen_is_not_watch_actionable(board):
    run(board, "join", "planner", "--roles", "leadership", agent="planner")
    run(board, "join", "carol", "--roles", "backend", agent="carol")
    run(board, "master", "take", agent="planner")
    run(board, "create", "Work", "--role", "backend", agent="planner")
    _stamp_unknown_reopen(board, "T-002", to="carol")
    (board / "messages.jsonl").write_text(
        json.dumps({
            "id": "m-same", "kind": "task", "re": "T-002", "to": "carol",
            "from": "planner", "at": SAME, "text": "take T-002", "task": True,
        }) + "\n" + json.dumps({
            "id": "m-stuck", "kind": "task", "re": "T-002", "to": "planner",
            "from": "carol", "at": SAME, "text": "stuck: T-002 unknown",
        }) + "\n"
    )
    tk = _mod()
    pending = tk.pending_work(str(board), "carol")
    assert "task_messages" not in pending, pending
    assert "ready_in_my_lane" not in pending, pending
    assert "suggested_for_me" not in pending, pending
    assert tk.actionable(pending) is False, pending
    fp = tk._watch_trigger_fingerprint(str(board), "carol", pending)
    assert not fp, fp

    master_pending = tk.pending_work(str(board), "planner")
    assert "stuck_messages" not in master_pending, master_pending
    master_fp = tk._watch_trigger_fingerprint(str(board), "planner", master_pending)
    if master_fp:
        assert not any(part.startswith("stuck:") for part in master_fp), master_fp
