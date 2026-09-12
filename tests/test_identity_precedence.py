"""whoami() vs session_seat(), and the self-addressed-message guard.

Two functions answer two different questions, and conflating them is the
regression this file exists to pin:

  * whoami(explicit): who does THIS ONE COMMAND act as. explicit > TICKET_SEAT
    > TICKET_AGENT > pid. Never consults a session's recorded identity (from
    `tickets join`), and never discovers the board. This is what makes
    `TICKET_AGENT=alice tickets note T-001 "..."` a working per-invocation
    override even inside a session that separately joined as someone else.

  * session_seat(board, explicit): who is THIS SESSION. explicit > this
    session's own recorded identity > TICKET_SEAT > TICKET_AGENT > pid. Only
    four surfaces need this: `board`'s "you:" line, the stop-hook,
    `msg`'s sender, and `inbox`'s owner, all with no --owner given -- because
    for those, "who is sending/reading this" IS "who is this session".

Widening either function to answer the other's question was tried and
reverted: making whoami() consult the board broke the documented
`TICKET_AGENT=X tickets <cmd>` per-invocation pattern (a recorded join
silently outranked an explicit override); making session_seat() forget the
record breaks attribution for `tickets join planner` itself.
"""

import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TICKETS = os.path.join(os.path.dirname(HERE), "tickets.py")


def run(board, args, env=None, session=None, agent=None):
    e = dict(os.environ)
    for var in (
        "TICKET_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        "CODEX_SESSION_ID",
        "CURSOR_SESSION_ID",
        "TERM_SESSION_ID",
    ):
        e.pop(var, None)
    e.pop("TICKET_AGENT", None)
    e.pop("TICKET_SEAT", None)
    e.pop("TICKETS_DIR", None)
    if session is not None:
        e["TICKET_SESSION_ID"] = session
    if agent is not None:
        e["TICKET_AGENT"] = agent
    e.update(env or {})
    if "TICKETS_DIR" not in e:
        e["TICKETS_DIR"] = os.path.join(board, ".tickets")
    return subprocess.run(
        [sys.executable, TICKETS] + args,
        cwd=board,
        env=e,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def board(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    run(str(d), ["init"], session="setup")
    return str(d)


def _senders(board):
    path = os.path.join(board, ".tickets", "messages.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line).get("from") for line in f if line.strip()]


def _notes(board, tid):
    with open(os.path.join(board, ".tickets", tid + ".json")) as f:
        return json.load(f).get("notes", [])


def test_join_records_identity_and_it_outranks_the_env_var(board):
    """The session's own record beats a foreign TICKET_AGENT for THIS session."""
    run(board, ["join", "planner", "--roles", "review"], session="s1")
    r = run(board, ["msg", "hello"], session="s1", agent="some-other-worker")
    assert r.returncode == 0, r.stderr
    assert _senders(board)[-1] == "planner"


def test_explicit_owner_still_wins_over_the_recorded_identity(board):
    run(board, ["join", "planner", "--roles", "review"], session="s1")
    r = run(board, ["msg", "hello", "--owner", "someone-else"], session="s1")
    assert r.returncode == 0, r.stderr
    assert _senders(board)[-1] == "someone-else"


def test_env_var_is_still_honoured_when_nothing_is_recorded(board):
    r = run(board, ["msg", "hello"], session="s2", agent="env-worker")
    assert r.returncode == 0, r.stderr
    assert _senders(board)[-1] == "env-worker"


def test_whoami_surfaces_ignore_a_join_this_same_session_made(board):
    """whoami() (unlike session_seat()) must NOT consult the recorded seat --
    that is the whole reason the two functions are split. `note` is a
    whoami()-only surface: even inside the session that ran `join planner`,
    an explicit per-invocation TICKET_AGENT must still be who the note is
    attributed to, or the documented single-command override silently stops
    working the moment a session has ever joined.
    """
    run(board, ["join", "planner", "--roles", "review"], session="s1")
    r = run(board, ["create", "t1", "--role", "review"], session="s1")
    assert r.returncode == 0, r.stderr
    tid = r.stdout.split()[1]
    r = run(board, ["note", tid, "hello from note"], session="s1", agent="someone-else")
    assert r.returncode == 0, r.stderr
    assert _notes(board, tid)[-1]["by"] == "someone-else"


def test_plain_self_addressed_message_warns_but_still_posts(board):
    """A plain DM to yourself does not wake anyone (only --task, or landing
    on a `continuous`-mode seat, does), so it reads as a harmless note to
    self far more often than as the coordinator mistake this guard exists
    for. Warn, but do not block a command that may have been intentional.
    """
    run(board, ["join", "planner", "--roles", "review"], session="s1")
    before = len(_senders(board))
    r = run(board, ["msg", "--to", "planner", "do the thing"], session="s1")
    assert r.returncode == 0, r.stdout
    assert "your OWN identity" in (r.stderr + r.stdout)
    assert len(_senders(board)) == before + 1


def test_task_self_addressed_message_is_refused_and_names_the_identity(board):
    """A --task send to yourself WOULD wake you, which is the destructive
    case: it is almost always a misdirected assignment meant for someone
    else, and it fails SILENTLY otherwise -- the message posts, the
    sender's own unread count rises, and their own inbox hook reports their
    own assignment back, which reads exactly like a peer receiving mail and
    not replying.
    """
    run(board, ["join", "planner", "--roles", "review"], session="s1")
    before = len(_senders(board))
    r = run(board, ["msg", "--to", "planner", "--task", "do the thing"], session="s1")
    assert r.returncode != 0, r.stdout
    assert "your OWN identity" in (r.stderr + r.stdout)
    assert "planner" in (r.stderr + r.stdout)
    # a refused send must not leave a message behind
    assert len(_senders(board)) == before


def test_addressing_someone_else_is_unaffected(board):
    run(board, ["join", "planner", "--roles", "review"], session="s1")
    r = run(board, ["msg", "--to", "worker-1", "do the thing"], session="s1")
    assert r.returncode == 0, r.stderr
    assert _senders(board)[-1] == "planner"
