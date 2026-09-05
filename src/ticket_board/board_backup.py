#!/usr/bin/env python3
"""Fixture-board backup and restore for the shared tickets board.

Never targets a live board unless --i-understand-live is passed (discouraged).
Excludes secrets-ish paths and message payloads. Does not reassign tickets.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

INCLUDE_NAMES = {
    "master.json",
    "MASTER.md",
    "CONTEXT.md",
    "roles.json",
    "merge.json",
    "IDS.md",
}
INCLUDE_GLOBS = (
    "T-*.json",
    "E-*.json",
    "S-*.json",
    "agents/*.json",
    "briefs/*",
    "coordination/state.json",
)
EXCLUDE_SUBSTRINGS = (
    "secret",
    "credential",
    "password",
    "token",
    ".env",
    "api_key",
    "apikey",
)
EXCLUDE_NAMES = {
    "messages.jsonl",
    "messages.read",
    "merge.lock",
    "watch.lock",
    "state.lock",
}


def is_excluded(path: Path, root: Path) -> bool:
    rel = str(path.relative_to(root)).lower()
    name = path.name.lower()
    if name in EXCLUDE_NAMES or name.endswith(".lock") or name.endswith(".tmp"):
        return True
    if any(s in rel for s in EXCLUDE_SUBSTRINGS):
        return True
    if re.search(r"(^|/)\.env", rel):
        return True
    return False


def collect_files(board: Path) -> list[Path]:
    out: list[Path] = []
    for name in INCLUDE_NAMES:
        p = board / name
        if p.is_file() and not is_excluded(p, board):
            out.append(p)
    for pattern in INCLUDE_GLOBS:
        for p in board.glob(pattern):
            if p.is_file() and not is_excluded(p, board):
                out.append(p)
    # stable unique
    seen = set()
    unique = []
    for p in sorted(out, key=lambda x: str(x)):
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def scrub_ticket(data: dict) -> dict:
    """Drop free-text that might contain secrets; keep structure for restore drill."""
    clean = dict(data)
    notes = []
    for note in data.get("notes") or []:
        notes.append(
            {
                "by": note.get("by"),
                "at": note.get("at"),
                "text": "[redacted-for-backup]",
            }
        )
    if "notes" in clean:
        clean["notes"] = notes
    return clean


def backup(board: Path, archive: Path, *, allow_live: bool) -> dict:
    if not board.is_dir():
        raise SystemExit(f"board missing: {board}")
    marker = board / ".fixture-board"
    if not marker.exists() and not allow_live:
        raise SystemExit(
            "refusing: board has no .fixture-board marker. "
            "Create a fixture board or pass --i-understand-live (not for routine drills)."
        )
    archive.parent.mkdir(parents=True, exist_ok=True)
    files = collect_files(board)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "board": str(board),
        "file_count": len(files),
        "files": [str(p.relative_to(board)) for p in files],
        "excludes": sorted(EXCLUDE_NAMES) + list(EXCLUDE_SUBSTRINGS),
        "redacts_note_text": True,
        "auto_reassign": False,
    }
    tmp = archive.with_suffix(archive.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    with tarfile.open(tmp, "w:gz") as tar:
        info = tarfile.TarInfo("manifest.json")
        payload = (json.dumps(manifest, indent=2) + "\n").encode()
        info.size = len(payload)
        import io

        tar.addfile(info, io.BytesIO(payload))
        for path in files:
            rel = path.relative_to(board).as_posix()
            if path.name.startswith("T-") and path.suffix == ".json":
                data = scrub_ticket(json.loads(path.read_text()))
                blob = (json.dumps(data, indent=2) + "\n").encode()
                ti = tarfile.TarInfo(rel)
                ti.size = len(blob)
                tar.addfile(ti, io.BytesIO(blob))
            else:
                tar.add(path, arcname=rel)
    tmp.replace(archive)
    return manifest | {"archive": str(archive)}


def restore(archive: Path, dest: Path, *, allow_live: bool) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    marker = dest / ".fixture-board"
    if not marker.exists() and not allow_live:
        raise SystemExit(
            "refusing restore into non-fixture board. "
            "Write .fixture-board first or pass --i-understand-live."
        )
    with tarfile.open(archive, "r:gz") as tar:
        # Python 3.12+ filter; fall back for older.
        try:
            tar.extractall(dest, filter=tarfile.data_filter)
        except (AttributeError, TypeError):
            tar.extractall(dest)
    marker.write_text("fixture\n")
    restored = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
    return {
        "archive": str(archive),
        "dest": str(dest),
        "restored_files": len(restored),
        "sample": restored[:20],
        "auto_reassign": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--board", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--i-understand-live", action="store_true")
    r = sub.add_parser("restore")
    r.add_argument("--archive", type=Path, required=True)
    r.add_argument("--dest", type=Path, required=True)
    r.add_argument("--i-understand-live", action="store_true")
    args = p.parse_args()
    if args.cmd == "backup":
        print(json.dumps(backup(args.board, args.out, allow_live=args.i_understand_live), indent=2))
        return 0
    if args.cmd == "restore":
        print(
            json.dumps(
                restore(args.archive, args.dest, allow_live=args.i_understand_live),
                indent=2,
            )
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
