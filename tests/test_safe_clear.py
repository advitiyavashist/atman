"""T-108: safe clear + backup/restore on fixture boards only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[1] / "src" / "ticket_board" / "cli.py"


def run_tickets(board: Path, *args: str, check: bool = False):
    env = os.environ.copy()
    env["TICKETS_DIR"] = str(board)
    env["TICKET_AGENT"] = "tester"
    env["PYTHONPATH"] = str(CLI.parent) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=board.parent,
    )


@pytest.fixture
def live_board(tmp_path):
    board = tmp_path / ".tickets"
    board.mkdir()
    (board / "T-1.json").write_text(json.dumps({"id": "T-1", "status": "open", "notes": []}))
    (board / "messages.jsonl").write_text("{}\n")
    return board


@pytest.fixture
def fixture_board(tmp_path):
    board = tmp_path / "fixture"
    board.mkdir()
    (board / ".fixture-board").write_text("fixture\n")
    (board / "T-1.json").write_text(json.dumps({"id": "T-1", "status": "open", "notes": []}))
    (board / "T-2.json").write_text(json.dumps({"id": "T-2", "status": "claimed", "notes": []}))
    return board


def test_clear_refuses_live_board(live_board):
    r = run_tickets(live_board, "clear", "--yes")
    assert r.returncode != 0
    assert "REFUSED" in (r.stdout + r.stderr)
    assert (live_board / "T-1.json").exists()


def test_clear_fixture_requires_yes(fixture_board):
    r = run_tickets(fixture_board, "clear")
    assert r.returncode != 0
    assert (fixture_board / "T-1.json").exists()


def test_clear_fixture_with_yes(fixture_board):
    r = run_tickets(fixture_board, "clear", "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (fixture_board / "T-1.json").exists()
    assert not (fixture_board / "T-2.json").exists()
    assert (fixture_board / ".fixture-board").exists()


def test_backup_restore_roundtrip(fixture_board, tmp_path):
    (fixture_board / "master.json").write_text(json.dumps({"owner": "ceo"}))
    (fixture_board / "agents").mkdir()
    (fixture_board / "agents" / "cursor.json").write_text(json.dumps({"seen": "x"}))
    (fixture_board / "coordination").mkdir()
    (fixture_board / "coordination" / "state.json").write_text(
        json.dumps({"schema": 1, "roles": {}, "handovers": []})
    )
    archive = tmp_path / "b.tgz"
    r = run_tickets(fixture_board, "board-backup", "--out", str(archive))
    assert r.returncode == 0, r.stdout + r.stderr
    dest = tmp_path / "restored"
    dest.mkdir()
    (dest / ".fixture-board").write_text("fixture\n")
    r2 = run_tickets(fixture_board, "board-restore", "--archive", str(archive), "--dest", str(dest))
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert (dest / "T-1.json").exists()
    assert (dest / "master.json").exists()
