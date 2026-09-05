"""Basic claim / done lifecycle used in production."""

from __future__ import annotations

from pathlib import Path

from tickets.board import Board

from tests.conftest import run


def test_claim_specific_then_done(empty_board: Path) -> None:
    assert run("create", "First task", "-b", "do the thing", "--priority", "p1", board=empty_board) == 0
    assert run("create", "Second task", board=empty_board, agent="bob") == 0

    code = run("claim", "T-1", board=empty_board, agent="alice")
    assert code == 0
    ticket = Board(empty_board).load("T-1")
    assert ticket.status == "claimed"
    assert ticket.claimed_by == "alice"
    assert ticket.assignee == "alice"

    code = run("done", "T-1", "shipped it", board=empty_board, agent="alice")
    assert code == 0
    ticket = Board(empty_board).load("T-1")
    assert ticket.status == "done"
    assert any(n.get("kind") == "done" for n in ticket.notes)
    assert any("shipped it" in (n.get("text") or "") for n in ticket.notes)


def test_claim_next_skips_blocked_deps(empty_board: Path) -> None:
    assert run("create", "Foundation", board=empty_board) == 0
    assert run("create", "Depends", "--dep", "T-1", board=empty_board) == 0

    # T-2 is blocked by T-1; next/claim should take T-1.
    assert run("claim", board=empty_board, agent="alice") == 0
    t1 = Board(empty_board).load("T-1")
    t2 = Board(empty_board).load("T-2")
    assert t1.claimed_by == "alice"
    assert t2.status == "open"
    assert t2.holder() is None

    # Claiming T-2 while T-1 is unfinished must fail.
    assert run("claim", "T-2", board=empty_board, agent="bob") == 2
    assert Board(empty_board).load("T-2").holder() is None

    assert run("done", "T-1", board=empty_board, agent="alice") == 0
    assert run("claim", "T-2", board=empty_board, agent="bob") == 0
    assert Board(empty_board).load("T-2").claimed_by == "bob"


def test_double_claim_rejected(empty_board: Path) -> None:
    assert run("create", "Only one owner", board=empty_board) == 0
    assert run("claim", "T-1", board=empty_board, agent="alice") == 0
    assert run("claim", "T-1", board=empty_board, agent="bob") == 2
    assert Board(empty_board).load("T-1").claimed_by == "alice"


def test_unknown_ticket_fields_are_preserved(empty_board: Path) -> None:
    path = empty_board / "T-1.json"
    path.write_text(
        '{"id":"T-1","title":"x","status":"open","notes":[],"steer_only":{"k":1}}\n',
        encoding="utf-8",
    )
    assert run("note", "T-1", "hello", board=empty_board, agent="alice") == 0
    data = Board(empty_board).load("T-1").to_dict()
    assert data["steer_only"] == {"k": 1}


def test_pulse_and_list(empty_board: Path) -> None:
    assert run("create", "A", board=empty_board) == 0
    assert run("claim", "T-1", board=empty_board, agent="alice") == 0
    assert run("pulse", board=empty_board, agent="alice") == 0
    assert run("list", board=empty_board) == 0
    assert run("who", board=empty_board) == 0
    assert run("show", "T-1", board=empty_board) == 0
