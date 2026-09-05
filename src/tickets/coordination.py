"""File locking, agent identity, and presence (multi-agent coordination)."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any, Iterator

from tickets.models import utcnow
from tickets.paths import HERE_NAME, LOCK_NAME, PRESENCE_DIRNAME

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]


class BoardLock:
    """Exclusive flock on ``<board>/.lock``. No-op lock file still created."""

    def __init__(self, board: Path) -> None:
        self.board = board
        self.path = board / LOCK_NAME
        self._fh = None

    def __enter__(self) -> BoardLock:
        self.board.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._fh is not None:
            if fcntl is not None:
                try:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            self._fh.close()
            self._fh = None


def resolve_agent(explicit: str | None = None, board: Path | None = None) -> str:
    """Identity: ``--as``, ``TICKETS_AGENT``, board ``here`` file, then ``$USER``."""
    if explicit and explicit.strip():
        return explicit.strip()
    env = os.environ.get("TICKETS_AGENT", "").strip()
    if env:
        return env
    if board is not None:
        here = read_here(board)
        if here:
            return here
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "agent"
    return user.strip() or "agent"


def here_path(board: Path) -> Path:
    return board / HERE_NAME


def read_here(board: Path) -> str | None:
    path = here_path(board)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def write_here(board: Path, agent: str) -> None:
    board.mkdir(parents=True, exist_ok=True)
    here_path(board).write_text(agent + "\n", encoding="utf-8")


def presence_dir(board: Path) -> Path:
    return board / PRESENCE_DIRNAME


def write_presence(
    board: Path,
    agent: str,
    *,
    ticket_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    directory = presence_dir(board)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "agent": agent,
        "at": utcnow(),
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "ticket": ticket_id,
    }
    if extra:
        payload.update(extra)
    path = directory / f"{_safe_name(agent)}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def read_presence(board: Path) -> list[dict[str, Any]]:
    directory = presence_dir(board)
    if not directory.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return rows


def _safe_name(agent: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in agent) or "agent"


def locked(board: Path) -> Iterator[BoardLock]:
    with BoardLock(board) as lock:
        yield lock
