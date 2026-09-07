"""T-418: packaged cli.py checkin() must not wipe inbox_seen or joined_at.

The root tickets.py fix at 9c5c606 merged into the existing agent record;
cli.py still rebuilds a fresh dict and os.replace()s it, wiping every field
checkin() does not know about. The board test suite drives the root copy, so
this defect is invisible to test_wakeup::test_checkin_preserves_*.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG_CLI = ROOT / "src" / "ticket_board" / "cli.py"


def load_cli():
    spec = importlib.util.spec_from_file_location("ticket_board_cli", PKG_CLI)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT))
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    b = repo / ".tickets"
    agents = b / "agents"
    agents.mkdir(parents=True)
    return b


def test_packaged_checkin_preserves_inbox_seen_and_joined_at(board):
    """RED on broken cli.py: checkin() rebuilds the record and drops both fields."""
    mod = load_cli()
    rec_path = board / "agents" / "bob.json"
    rec_path.write_text(
        json.dumps(
            {
                "owner": "bob",
                "inbox_seen": "2026-09-07T12:00:00Z",
                "joined_at": "2026-09-07T11:00:00Z",
                "limit": {"note": "out"},
            }
        )
    )
    mod.checkin(str(board), "bob")
    after = json.loads(rec_path.read_text())
    assert after.get("inbox_seen") == "2026-09-07T12:00:00Z", after
    assert after.get("joined_at") == "2026-09-07T11:00:00Z", after
    assert after.get("limit") == {"note": "out"}, after
