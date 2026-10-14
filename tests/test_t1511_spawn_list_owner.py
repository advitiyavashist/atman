"""T-1511: `atm spawn --list` must not crash on an agents/*.json without an owner."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _mod():
    spec = importlib.util.spec_from_file_location("tickets_t1511", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(board, *args, agent="lead"):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent,
             HOME=str(board.parent.parent / "home"), TICKET_SESSION_ID="t1511-%s" % agent)
    for var in ("TICKETS_STOP_HOOK", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID",
                "CURSOR_SESSION_ID", "TERM_SESSION_ID", "TICKET_SEAT"):
        e.pop(var, None)
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                          env=e, cwd=str(board.parent))


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    b = repo / ".tickets"
    r = run(b, "create", "Write docs", "--role", "docs")
    assert r.returncode == 0, r.stderr
    r = run(b, "join", "worker-a", "--roles", "docs", agent="worker-a")
    assert r.returncode == 0, r.stderr
    return b


def test_list_survives_record_without_owner(board):
    # The shape found on the live board: a wake receipt and nothing else.
    (board / "agents" / "ceo.json").write_text(json.dumps(
        {"wake_delivery": {"message_id": "m1", "label": "no live endpoint",
                           "poked": False, "at": "2026-09-22T05:31:40Z"}}))
    (board / "agents" / "broken.json").write_text("{not json")
    r = run(board, "spawn", "--list")
    assert r.returncode == 0, r.stderr
    assert "KeyError" not in r.stderr
    rows = r.stdout.splitlines()
    assert any(line.startswith("worker-a ") for line in rows), r.stdout
    assert any(line.startswith("ceo !") for line in rows), r.stdout
    assert any(line.startswith("broken !") for line in rows), r.stdout
    assert "malformed agent record ceo: no owner field" in r.stdout
    assert "malformed agent record broken: unreadable json" in r.stdout
    assert "malformed agent record worker-a" not in r.stdout


def test_wake_receipt_does_not_create_an_ownerless_record(board):
    mod = _mod()
    assert mod._note_wake_delivery(str(board), "ceo", "no live endpoint", "m1") is None
    assert not (board / "agents" / "ceo.json").exists()
    rec = mod._note_wake_delivery(str(board), "worker-a", "no live endpoint", "m2")
    assert rec["wake_delivery"]["message_id"] == "m2"
    on_disk = json.loads((board / "agents" / "worker-a.json").read_text())
    assert on_disk["owner"] == "worker-a"
    assert on_disk["wake_delivery"]["message_id"] == "m2"
