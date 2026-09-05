"""Board path resolution and on-disk layout.

The live board is a directory (default ``.tickets/``) containing ``T-*.json``
ticket files plus optional coordination / presence / secret files.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

BOARD_DIRNAME = ".tickets"
BACKUPS_DIRNAME = ".tickets-backups"
LOCK_NAME = ".lock"
HERE_NAME = "here"
PRESENCE_DIRNAME = "presence"
INBOX_NAME = "inbox.jsonl"
BOARD_META_NAME = "board.json"
SECRETS_DIRNAMES = frozenset({"secrets", ".secrets"})
TICKET_NAME_RE = re.compile(r"^T-(\d+)\.json$", re.IGNORECASE)
TICKET_ID_RE = re.compile(r"^T-(\d+)$", re.IGNORECASE)

# Filenames / suffixes never copied into a board backup.
SECRET_FILENAMES = frozenset(
    {
        ".env",
        "secrets.json",
        "credentials.json",
        "token",
        ".token",
        "id_rsa",
        "id_ed25519",
    }
)
SECRET_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".env"})


def env_board() -> Path | None:
    raw = os.environ.get("TICKETS_BOARD") or os.environ.get("TICKETS_DIR")
    if not raw:
        return None
    return Path(raw).expanduser()


def find_board(start: Path | None = None) -> Path | None:
    """Walk upward from *start* (cwd by default) looking for ``.tickets``."""
    cur = (start or Path.cwd()).resolve()
    if cur.is_file():
        cur = cur.parent
    for directory in (cur, *cur.parents):
        candidate = directory / BOARD_DIRNAME
        if candidate.is_dir():
            return candidate
    return None


def resolve_board(explicit: str | Path | None = None, *, create: bool = False) -> Path:
    """Resolve the board directory.

    Priority: ``--board`` / explicit path, ``TICKETS_BOARD`` / ``TICKETS_DIR``,
    nearest ``.tickets`` walking up from cwd, else ``./.tickets``.
    """
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
    else:
        env = env_board()
        if env is not None:
            path = env.resolve()
        else:
            found = find_board()
            path = found if found is not None else (Path.cwd() / BOARD_DIRNAME).resolve()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def backups_dir(board: Path) -> Path:
    """Sibling ``.tickets-backups/`` so wipes never delete recovery snapshots."""
    return board.parent / BACKUPS_DIRNAME


def ticket_path(board: Path, ticket_id: str) -> Path:
    return board / f"{normalize_ticket_id(ticket_id)}.json"


def iter_ticket_paths(board: Path) -> list[Path]:
    if not board.is_dir():
        return []
    paths = [p for p in board.iterdir() if p.is_file() and TICKET_NAME_RE.match(p.name)]
    return sorted(paths, key=lambda p: ticket_number(p.stem))


def normalize_ticket_id(raw: str) -> str:
    text = raw.strip()
    match = TICKET_ID_RE.match(text)
    if not match:
        raise ValueError(f"invalid ticket id {raw!r} (expected T-<number>)")
    return f"T-{int(match.group(1))}"


def ticket_number(ticket_id: str) -> int:
    return int(TICKET_ID_RE.match(normalize_ticket_id(ticket_id)).group(1))  # type: ignore[union-attr]


def is_secret_path(board: Path, path: Path) -> bool:
    """True if *path* (relative to *board*) should be excluded from backups."""
    try:
        relative = path.resolve().relative_to(board.resolve())
    except ValueError:
        return True
    parts = relative.parts
    if any(part in SECRETS_DIRNAMES for part in parts):
        return True
    name = relative.name
    if name in SECRET_FILENAMES:
        return True
    if any(name.endswith(suffix) for suffix in SECRET_SUFFIXES):
        return True
    return False
