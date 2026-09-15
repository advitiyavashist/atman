"""T-955 train-3: tickets.py must not act on LIFE_UNKNOWN reopen posts."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board, run  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TICKETS = ROOT / "tickets.py"
SAME = "2026-09-14T12:00:00Z"


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
