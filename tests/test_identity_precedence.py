"""Identity must be a recorded fact, not an inherited env var.

The failure this prevents, observed in a real session: agents persisted their
identity by appending `TICKET_AGENT=<name>` to a shared shell profile, because
there was no supported way to record it. Every process on the machine then
inherited whichever value was written last -- so a coordinator silently became
one of its own workers, addressed its task assignments to its own handle, and
spent most of a session diagnosing the resulting silence as a dead peer, a
missing wake flag and a broken hook chain in turn.

Two guards: a recorded per-board identity outranks the ambient env var, and a
self-addressed message is refused outright.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TICKETS = str(Path(__file__).resolve().parent.parent / "tickets.py")


def run(board, *args, env=None):
    e = dict(os.environ)
    e.pop("TICKET_AGENT", None)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, TICKETS, *args],
                          cwd=str(board), capture_output=True, text=True, env=e)


@pytest.fixture()
def board(tmp_path):
    b = tmp_path / "b"
    b.mkdir()
    run(b, "init")
    return b


def _senders(board):
    p = board / ".tickets" / "messages.jsonl"
    if not p.exists():
        return []
    return [json.loads(l).get("from") for l in p.read_text().splitlines() if l.strip()]


def test_join_records_identity_and_it_outranks_the_env_var(board):
    run(board, "join", "planner", "--roles", "review")
    # a foreign TICKET_AGENT must NOT be able to reassign who this checkout is
    r = run(board, "msg", "hello", env={"TICKET_AGENT": "some-other-worker"})
    assert r.returncode == 0, r.stderr
    assert _senders(board)[-1] == "planner", _senders(board)


def test_explicit_owner_still_wins_over_the_recorded_identity(board):
    run(board, "join", "planner", "--roles", "review")
    r = run(board, "msg", "hello", "--owner", "someone-else")
    assert r.returncode == 0, r.stderr
    assert _senders(board)[-1] == "someone-else"


def test_env_var_is_still_honoured_when_nothing_is_recorded(board):
    r = run(board, "msg", "hello", env={"TICKET_AGENT": "env-worker"})
    assert r.returncode == 0, r.stderr
    assert _senders(board)[-1] == "env-worker"


def test_self_addressed_message_is_refused_and_names_the_identity(board):
    run(board, "join", "planner", "--roles", "review")
    before = len(_senders(board))
    r = run(board, "msg", "--to", "planner", "do the thing")
    assert r.returncode != 0, r.stdout
    assert "your OWN identity" in (r.stderr + r.stdout)
    assert "planner" in (r.stderr + r.stdout)
    # nothing was posted -- a refused send must not leave a message behind
    assert len(_senders(board)) == before


def test_addressing_someone_else_is_unaffected(board):
    run(board, "join", "planner", "--roles", "review")
    r = run(board, "msg", "--to", "worker-1", "do the thing")
    assert r.returncode == 0, r.stderr
    assert _senders(board)[-1] == "planner"
