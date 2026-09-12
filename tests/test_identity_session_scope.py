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
    # Strip every session var so a test never inherits the real harness's id
    # (this suite itself runs inside a coding-agent session that sets one).
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
    # `board` is deliberately silent on an empty board, so the global hook
    # stays inert in unrelated directories. Give it something to print.
    run(str(d), ["create", "seat probe", "--role", "lead"], session="setup")
    return str(d)


def seat_of(board, **kw):
    out = run(board, ["board", "-q"], **kw).stdout
    for line in out.splitlines():
        if "you: " in line:
            seat = line.split("you: ", 1)[1].split(" |")[0].strip()
            return seat.replace("(UNCONFIRMED)", "").strip()
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


def test_unconfirmed_seat_is_flagged_and_the_agent_is_told_what_to_do(board):
    """An agent that does not know its seat cannot decline another seat's mail.

    So the ambiguity must be stated, not implied -- and stated with the remedy,
    because the reader here is usually an agent deciding whether a message is
    addressed to it.
    """
    out = run(board, ["board", "-q"], session="fresh", agent="ambient").stdout
    assert "(UNCONFIRMED)" in out
    assert "is a guess from the environment" in out
    assert "tickets join" in out

    run(board, ["join", "alpha", "--roles", "lead"], session="fresh")
    confirmed = run(board, ["board", "-q"], session="fresh").stdout
    assert "(UNCONFIRMED)" not in confirmed
    assert "you: alpha" in confirmed


def test_inbox_is_silent_for_a_session_with_no_recorded_seat(board):
    """For hooks: silence beats handing a session another seat's backlog.

    The original failure was a hook printing one agent's unread mail into
    another agent's window, where it was read and acted on. If identity cannot
    be pinned, printing nothing is the only safe output.
    """
    run(board, ["join", "alpha", "--roles", "lead"], session="session-A")
    run(board, ["join", "beta", "--roles", "ds"], session="session-B")
    run(board, ["msg", "for alpha only", "--to", "alpha"], session="session-B")

    quiet = run(
        board,
        ["inbox", "--keep", "--limit", "5", "--quiet-if-unidentified"],
        session="unrecorded-window",
        agent="alpha",  # the inherited-name trap: env says alpha, session did not
    )
    assert quiet.stdout.strip() == "", quiet.stdout
    assert "for alpha only" not in quiet.stdout

    # The seat that actually recorded itself still sees its own mail.
    mine = run(
        board,
        ["inbox", "--keep", "--limit", "5", "--quiet-if-unidentified"],
        session="session-A",
    )
    assert "for alpha only" in mine.stdout


def test_supervisor_assigned_seat_confirms_a_launched_run_with_no_join(board):
    """TICKET_SEAT is a supervisor's deliberate assignment for a run it is
    launching (watch/spawn), not ambient inheritance -- so it must confirm
    the seat even though the launched process has a brand-new session id
    and never called `join` itself. Otherwise a launched worker would be
    hidden from the very mail it was launched to handle.
    """
    run(board, ["join", "boss", "--roles", "lead"], session="session-boss")
    run(board, ["msg", "for worker-1 only", "--to", "worker-1"], session="session-boss")

    seen = run(
        board,
        ["inbox", "--keep", "--limit", "5", "--quiet-if-unidentified"],
        session="fresh-launched-worker",
        env={"TICKET_SEAT": "worker-1", "TICKET_AGENT": "worker-1"},
    )
    assert "for worker-1 only" in seen.stdout

    out = run(board, ["board", "-q"], session="fresh-launched-worker",
              env={"TICKET_SEAT": "worker-1", "TICKET_AGENT": "worker-1"}).stdout
    assert "(UNCONFIRMED)" not in out
    assert "you: worker-1" in out
