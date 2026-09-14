# Suggested regression tests for the T-876 finding. Written in the style of
# tests/test_identity_session_scope.py and meant to be appended to it: they use
# that module's existing `run`, `board` and `seat_of` helpers unchanged.


def test_a_refused_join_does_not_claim_the_seat(board):
    """A join the board REJECTS is not a declaration of identity.

    cmd_join recorded the seat before the guards that can refuse it, so every
    refusal path left the session answering as the name it had just been denied.
    """
    run(board, ["join", "alpha", "--tool", "claude", "--roles", "lead"],
        session="session-A")
    # A second harness may not reuse alpha; the seat guard refuses.
    r = run(board, ["join", "alpha", "--tool", "codex", "--roles", "lead"],
            session="session-B", agent="bravo")
    assert r.returncode != 0, "expected the seat guard to refuse this join"
    assert seat_of(board, session="session-B", agent="bravo") == "bravo"


def test_a_refused_join_does_not_consume_the_refused_seats_mail(board):
    """The refusal must not hand the caller another seat's unread DMs.

    Worse than impersonation: `inbox` marks what it prints as read, so the
    private message is lost to the agent it was addressed to.
    """
    run(board, ["join", "alpha", "--tool", "claude", "--roles", "lead"],
        session="session-A")
    run(board, ["join", "carol", "--tool", "cursor", "--roles", "ds"],
        session="session-C")
    run(board, ["msg", "private handover details", "--to", "alpha"],
        session="session-C")

    r = run(board, ["join", "alpha", "--tool", "codex", "--roles", "lead"],
            session="session-B", agent="bravo")
    assert r.returncode != 0, "expected the seat guard to refuse this join"

    out = run(board, ["inbox"], session="session-B", agent="bravo").stdout
    assert "for alpha" not in out, out
    assert "private handover details" not in out, out
    # ...and alpha still has it.
    assert "private handover details" in run(
        board, ["inbox"], session="session-A").stdout
