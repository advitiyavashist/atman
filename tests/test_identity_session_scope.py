"""Identity must be scoped to the agent SESSION, not the board or the shell.

Two failure modes are pinned here, and they are opposites -- which is why the
first attempt at a fix reproduced the bug in a different costume:

  * Shell-scoped identity BLEEDS DOWNWARD. TICKET_AGENT is inherited, so a
    session started from a shell that once held another agent's value answers
    to that agent's name and reports that agent's mail.
  * Board-scoped identity BLEEDS SIDEWAYS. Several sessions share one board, so
    a single file beside the board is whichever agent wrote it last.

Only a session-keyed record is unambiguous.
"""

import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TICKETS = os.path.join(os.path.dirname(HERE), "tickets.py")


def run(board, args, env=None, session=None, agent=None):
    e = dict(os.environ)
    # Strip every session var so a test never inherits the real harness's id.
    for var in (
        "TICKET_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        "CODEX_SESSION_ID",
        "CURSOR_SESSION_ID",
        "TERM_SESSION_ID",
    ):
        e.pop(var, None)
    e.pop("TICKET_AGENT", None)
    if session is not None:
        e["TICKET_SESSION_ID"] = session
    if agent is not None:
        e["TICKET_AGENT"] = agent
    e.update(env or {})
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
    # `board` is deliberately silent on an empty board, so the global hook
    # stays inert in unrelated directories. Give it something to print.
    run(str(d), ["create", "seat probe", "--role", "lead"], session="setup")
    return str(d)


def seat_of(board, **kw):
    out = run(board, ["board", "-q"], **kw).stdout
    for line in out.splitlines():
        if "you: " in line:
            seat = line.split("you: ", 1)[1].split(" |")[0].strip()
            return seat.replace("(unkeyed session)", "").strip()
    raise AssertionError("board printed no seat:\n" + out)


def test_recorded_identity_does_not_bleed_to_another_session(board):
    """The sideways leak. Session A joining must not rename session B."""
    run(board, ["join", "alpha", "--roles", "lead"], session="session-A")
    assert seat_of(board, session="session-A") == "alpha"

    # Session B shares the board but recorded nothing. It must NOT answer as
    # alpha; it falls through to its own ambient value.
    assert seat_of(board, session="session-B", agent="beta") == "beta"


def test_recorded_identity_outranks_a_stale_inherited_env_var(board):
    """The downward leak -- the one that put another seat's mail in my window."""
    run(board, ["join", "alpha", "--roles", "lead"], session="session-A")
    seat = seat_of(board, session="session-A", agent="somebody-elses-stale-name")
    assert seat == "alpha"


def test_each_session_keeps_its_own_recorded_identity(board):
    run(board, ["join", "alpha", "--roles", "lead"], session="session-A")
    run(board, ["join", "beta", "--roles", "ds"], session="session-B")
    assert seat_of(board, session="session-A") == "alpha"
    assert seat_of(board, session="session-B") == "beta"


def test_explicit_owner_still_wins_over_a_recorded_identity(board):
    run(board, ["join", "alpha", "--roles", "lead"], session="session-A")
    r = run(
        board,
        ["msg", "--owner", "override", "--to", "alpha", "hello"],
        session="session-A",
    )
    assert r.returncode == 0, r.stderr
    assert "override" in r.stdout


def test_legacy_flat_file_is_honoured_only_when_there_is_no_session_key(board):
    """Backwards compatibility, but deliberately narrow.

    A flat file beside the board predates session keying. Honour it when the
    harness gives us nothing better -- and ignore it when it does, because
    adopting a file some other agent wrote is the sideways leak.
    """
    tickets_dir = os.path.join(board, ".tickets")
    with open(os.path.join(tickets_dir, ".agent-identity"), "w") as f:
        f.write("legacy-name\n")

    # No session key anywhere: the flat file is the best available answer.
    assert seat_of(board, agent="ambient") == "legacy-name"

    # A keyed session must not adopt it.
    assert seat_of(board, session="session-A", agent="ambient") == "ambient"


def test_unkeyed_session_is_flagged_so_the_ambiguity_is_visible(board):
    """If we cannot key the session, say so rather than implying certainty."""
    out = run(board, ["board", "-q"], agent="ambient").stdout
    assert "unkeyed session" in out
    out_keyed = run(board, ["board", "-q"], session="session-A", agent="ambient").stdout
    assert "unkeyed session" not in out_keyed
