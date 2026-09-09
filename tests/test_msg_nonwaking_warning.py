"""A plain DM does not wake the addressee, so a quiet addressee gets a warning.

The failure this prevents: a sender DMs an agent repeatedly with plain `msg
--to`, gets no reply, concludes the agent is dead, and re-plans around a
worker that was simply never woken. The CLI's own help documents that only
`--task` wakes -- but documentation does not fire at the moment of the mistake.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

TICKETS = str(Path(__file__).resolve().parent.parent / "tickets.py")


def run(board, *args, **kw):
    return subprocess.run([sys.executable, TICKETS, *args],
                          cwd=str(board), capture_output=True, text=True, **kw)


@pytest.fixture()
def board(tmp_path):
    b = tmp_path / "b"
    b.mkdir()
    run(b, "init")
    return b


def _messages(board):
    p = board / ".tickets" / "messages.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def test_plain_dm_to_never_seen_addressee_warns_and_suggests_task(board):
    r = run(board, "msg", "--to", "ghost", "please pick this up", "--owner", "sender")
    assert r.returncode == 0, r.stderr
    assert "does NOT wake" in r.stdout, r.stdout
    assert "--task" in r.stdout
    assert "has never posted" in r.stdout
    # the message is still delivered -- the warning is advisory, not a block
    recs = [m for m in _messages(board) if m.get("to") == "ghost"]
    assert len(recs) == 1
    assert recs[0].get("kind") != "task"


def test_task_message_never_warns(board):
    r = run(board, "msg", "--to", "ghost", "--task", "do the thing", "--owner", "sender")
    assert r.returncode == 0, r.stderr
    assert "does NOT wake" not in r.stdout, r.stdout
    recs = [m for m in _messages(board) if m.get("to") == "ghost"]
    assert recs[0].get("kind") == "task"


def test_recently_active_addressee_is_not_warned_about(board):
    # the addressee posts, so it is demonstrably live and a plain DM is fine
    run(board, "msg", "I am here", "--owner", "livewire")
    r = run(board, "msg", "--to", "livewire", "fyi only", "--owner", "sender")
    assert r.returncode == 0, r.stderr
    assert "does NOT wake" not in r.stdout, r.stdout


def test_broadcast_is_never_warned_about(board):
    # no --to means everyone; there is no addressee whose liveness could matter
    r = run(board, "msg", "board-wide note", "--owner", "sender")
    assert r.returncode == 0, r.stderr
    assert "does NOT wake" not in r.stdout, r.stdout
