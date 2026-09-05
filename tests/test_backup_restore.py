"""Backup / restore of the fixture board, secrets excluded."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

from tickets.backup import backup_board, restore_board
from tickets.board import Board
from tickets.cli import main
from tickets.paths import iter_ticket_paths

from tests.conftest import run


def test_backup_excludes_secrets_and_includes_tickets(board_dir: Path, tmp_path: Path) -> None:
    archive = tmp_path / "snap.tar.gz"
    result = backup_board(board_dir, archive, reason="test")
    assert result == archive
    assert archive.is_file()

    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
    assert "board/T-1.json" in names
    assert "board/T-2.json" in names
    assert "board/T-3.json" in names
    assert "board/board.json" in names
    assert "board/inbox.jsonl" in names
    assert "manifest.json" in names
    secret_names = [n for n in names if "secret" in n.lower() or n.endswith("token")]
    assert secret_names == []

    extracted = tarfile.open(archive, "r:gz")
    with extracted:
        raw = extracted.extractfile("manifest.json")
        assert raw is not None
        manifest = json.loads(raw.read().decode())
    assert manifest["ticket_count"] == 3
    assert any("token" in e or "secrets" in e for e in manifest["excluded"])


def test_restore_roundtrip_on_fixture_board(board_dir: Path, tmp_path: Path) -> None:
    archive = tmp_path / "snap.tar.gz"
    backup_board(board_dir, archive, reason="fixture")

    t1 = Board(board_dir).load("T-1")
    seed = t1.notes[0]["text"]
    assert seed == "seed note — do not lose this"

    # Mutate the live board: drop a ticket, change a note-bearing file.
    (board_dir / "T-3.json").unlink()
    t1.title = "mutated"
    t1.notes.append({"at": "now", "by": "eve", "kind": "note", "text": "should vanish on restore"})
    Board(board_dir).save(t1)
    (board_dir / "T-99.json").write_text(
        json.dumps({"id": "T-99", "title": "ghost", "status": "open", "notes": []}),
        encoding="utf-8",
    )

    restore_board(board_dir, archive, replace=True)

    names = {p.name for p in iter_ticket_paths(board_dir)}
    assert names == {"T-1.json", "T-2.json", "T-3.json"}
    restored = Board(board_dir).load("T-1")
    assert restored.title == "Restore notes after wipe incident"
    assert restored.notes[0]["text"] == seed
    assert not any("should vanish" in (n.get("text") or "") for n in restored.notes)
    assert Board(board_dir).load("T-3").status == "done"
    # Secrets on the live board survive restore.
    assert (board_dir / "secrets" / "token").read_text().strip() == "super-secret-token-do-not-backup"


def test_cli_backup_restore(board_dir: Path, tmp_path: Path) -> None:
    archive = tmp_path / "cli.tar.gz"
    assert run("board-backup", str(archive), board=board_dir) == 0
    (board_dir / "T-2.json").unlink()
    assert run("board-restore", str(archive), board=board_dir) == 0
    assert {p.name for p in iter_ticket_paths(board_dir)} == {"T-1.json", "T-2.json", "T-3.json"}
    t2 = Board(board_dir).load("T-2")
    assert t2.claimed_by == "alice"
    # Restore itself writes a pre-restore snapshot.
    pre = list((board_dir.parent / ".tickets-backups").glob("*pre-restore*.tar.gz"))
    assert pre


def test_cli_backup_default_location(board_dir: Path) -> None:
    code = main(["--board", str(board_dir), "--as", "alice", "--json", "board-backup"])
    assert code == 0
    backups = list((board_dir.parent / ".tickets-backups").glob("board-*.tar.gz"))
    assert backups
