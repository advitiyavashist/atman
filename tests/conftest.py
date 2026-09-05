from __future__ import annotations

from pathlib import Path

import pytest

from tickets.backup import copy_board_tree
from tickets.cli import main

FIXTURE_BOARD = Path(__file__).parent / "fixtures" / "board"


@pytest.fixture
def board_dir(tmp_path: Path) -> Path:
    dest = tmp_path / ".tickets"
    copy_board_tree(FIXTURE_BOARD, dest)
    return dest


@pytest.fixture
def empty_board(tmp_path: Path) -> Path:
    dest = tmp_path / ".tickets"
    dest.mkdir()
    return dest


def run(*argv: str, board: Path | None = None, agent: str = "alice") -> int:
    args: list[str] = []
    if board is not None:
        args.extend(["--board", str(board)])
    args.extend(["--as", agent])
    args.extend(argv)
    return main(args)
