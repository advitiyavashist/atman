"""clear must never delete T-*.json — the 2026-09-06 incident."""

from __future__ import annotations

from pathlib import Path

from tickets import DESTROY_CONFIRM
from tickets.board import Board
from tickets.paths import iter_ticket_paths

from tests.conftest import run


def ticket_names(board: Path) -> set[str]:
    return {p.name for p in iter_ticket_paths(board)}


def test_clear_unclaims_current_agent_only(board_dir: Path) -> None:
    before = ticket_names(board_dir)
    assert before == {"T-1.json", "T-2.json", "T-3.json"}

    code = run("clear", board=board_dir, agent="alice")
    assert code == 0

    after = ticket_names(board_dir)
    assert after == before, "clear deleted ticket files"

    t2 = Board(board_dir).load("T-2")
    assert t2.status == "open"
    assert t2.claimed_by is None
    assert t2.assignee is None
    # Notes history, including the claim, must remain.
    kinds = [n.get("kind") for n in t2.notes]
    assert "claim" in kinds
    assert "unclaim" in kinds
    assert any("claimed by alice" in (n.get("text") or "") for n in t2.notes)

    # Other tickets untouched.
    t1 = Board(board_dir).load("T-1")
    assert t1.status == "open"
    assert t1.notes[0]["text"] == "seed note — do not lose this"
    t3 = Board(board_dir).load("T-3")
    assert t3.status == "done"


def test_clear_with_ticket_flag_unclaims_one(board_dir: Path) -> None:
    code = run("clear", "--ticket", "T-2", board=board_dir, agent="alice")
    assert code == 0
    assert ticket_names(board_dir) == {"T-1.json", "T-2.json", "T-3.json"}
    t2 = Board(board_dir).load("T-2")
    assert t2.status == "open"
    assert t2.holder() is None


def test_clear_does_not_touch_others_tickets(board_dir: Path) -> None:
    code = run("clear", board=board_dir, agent="bob")
    assert code == 0
    t2 = Board(board_dir).load("T-2")
    assert t2.status == "claimed"
    assert t2.claimed_by == "alice"
    assert ticket_names(board_dir) == {"T-1.json", "T-2.json", "T-3.json"}


def test_clear_all_is_rejected_and_does_not_wipe(board_dir: Path) -> None:
    code = run("clear", "--all", board=board_dir, agent="alice")
    assert code == 2
    assert ticket_names(board_dir) == {"T-1.json", "T-2.json", "T-3.json"}
    t2 = Board(board_dir).load("T-2")
    assert t2.status == "claimed"
    assert t2.claimed_by == "alice"


def test_clear_wipe_alias_rejected(board_dir: Path) -> None:
    assert run("clear", "--wipe", board=board_dir) == 2
    assert ticket_names(board_dir) == {"T-1.json", "T-2.json", "T-3.json"}


def test_board_reset_without_flags_refuses(board_dir: Path) -> None:
    assert run("board-reset", board=board_dir) == 2
    assert run("board-reset", "--i-understand-destroy-all-tickets", board=board_dir) == 2
    assert run("board-reset", "--confirm", DESTROY_CONFIRM, board=board_dir) == 2
    assert ticket_names(board_dir) == {"T-1.json", "T-2.json", "T-3.json"}


def test_board_reset_with_both_confirmations_wipes_tickets_only(board_dir: Path) -> None:
    secret = board_dir / "secrets" / "token"
    assert secret.is_file()
    code = run(
        "board-reset",
        "--i-understand-destroy-all-tickets",
        "--confirm",
        DESTROY_CONFIRM,
        board=board_dir,
        agent="cos",
    )
    assert code == 0
    assert ticket_names(board_dir) == set()
    assert secret.is_file()
    assert secret.read_text() == "super-secret-token-do-not-backup"
    # Recovery snapshot must exist beside the board, not inside it.
    backups = list((board_dir.parent / ".tickets-backups").glob("*.tar.gz"))
    assert backups
