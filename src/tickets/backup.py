"""Board backup / restore. Secrets are never archived."""

from __future__ import annotations

import io
import json
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tickets.paths import backups_dir, is_secret_path, iter_ticket_paths

MANIFEST_NAME = "manifest.json"


class BackupError(Exception):
    pass


def default_archive_path(board: Path, *, reason: str = "manual") -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_reason = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in reason) or "backup"
    directory = backups_dir(board)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"board-{stamp}-{safe_reason}.tar.gz"


def backup_board(board: Path, dest: Path | None = None, *, reason: str = "manual") -> Path:
    """Snapshot *board* to a gzipped tar, excluding secrets.

    Ticket JSON, notes, inbox, presence, and board metadata are included.
    ``secrets/``, credential filenames, and key material are omitted.
    """
    if not board.exists():
        raise BackupError(f"board does not exist: {board}")
    archive = dest or default_archive_path(board, reason=reason)
    archive.parent.mkdir(parents=True, exist_ok=True)

    included: list[str] = []
    excluded: list[str] = []
    board_resolved = board.resolve()

    def _should_add(path: Path) -> bool:
        if path.is_dir():
            return not is_secret_path(board, path)
        if is_secret_path(board, path):
            excluded.append(str(path.relative_to(board_resolved)))
            return False
        return True

    tmp = archive.with_suffix(archive.suffix + ".partial")
    try:
        with tarfile.open(tmp, "w:gz") as tar:
            if board.is_dir():
                for path in sorted(board.rglob("*")):
                    if not _should_add(path):
                        continue
                    if path.is_file():
                        rel = path.relative_to(board_resolved)
                        tar.add(path, arcname=str(Path("board") / rel))
                        included.append(str(rel))
            manifest = {
                "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "reason": reason,
                "board": str(board_resolved),
                "ticket_count": len(iter_ticket_paths(board)),
                "included": included,
                "excluded": excluded,
            }
            manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
            info = tarfile.TarInfo(name=MANIFEST_NAME)
            info.size = len(manifest_bytes)
            tar.addfile(info, fileobj=io.BytesIO(manifest_bytes))
        tmp.replace(archive)
    finally:
        if tmp.exists():
            tmp.unlink()
    return archive


def restore_board(board: Path, archive: Path, *, replace: bool = True) -> dict[str, Any]:
    """Restore ticket files from *archive* into *board*.

    Existing ticket JSON is replaced. Secrets already on the live board are
    left untouched. Non-secret board files present in the archive are restored.
    """
    archive = archive.expanduser().resolve()
    if not archive.is_file():
        raise BackupError(f"backup not found: {archive}")
    board.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        names = [m.name for m in members]
        if MANIFEST_NAME not in names and not any(n.startswith("board/") for n in names):
            raise BackupError(f"{archive} is not a tickets board backup")
        manifest: dict[str, Any] = {}
        man_member = tar.getmember(MANIFEST_NAME) if MANIFEST_NAME in names else None
        if man_member is not None:
            extracted = tar.extractfile(man_member)
            if extracted is not None:
                manifest = json.loads(extracted.read().decode("utf-8"))

        for member in members:
            if member.name == MANIFEST_NAME or not member.isfile():
                continue
            rel = _archive_relpath(member.name)
            if rel is None:
                continue
            dest = (board / rel).resolve()
            try:
                dest.relative_to(board.resolve())
            except ValueError as exc:
                raise BackupError(f"refusing to extract {member.name} outside board") from exc
            if is_secret_path(board, dest):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                continue
            dest.write_bytes(src.read())

    if replace:
        # Remove ticket files that were not in the archive so restore is a
        # true snapshot rollback (notes history comes back intact).
        archived_tickets = {
            Path(_archive_relpath(n) or "").name
            for n in names
            if _archive_relpath(n) and Path(_archive_relpath(n) or "").name.endswith(".json")
        }
        for path in iter_ticket_paths(board):
            if path.name not in archived_tickets:
                path.unlink()

    return manifest


def _archive_relpath(name: str) -> str | None:
    normalized = name.replace("\\", "/").lstrip("/")
    if normalized == "board" or normalized == "board/":
        return None
    if normalized.startswith("board/"):
        rest = normalized[len("board/") :]
        if not rest or rest.endswith("/"):
            return None
        if ".." in Path(rest).parts:
            return None
        return rest
    return None


def copy_board_tree(src: Path, dest: Path) -> None:
    """Test helper: copy a fixture board, including secrets (not a backup)."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
