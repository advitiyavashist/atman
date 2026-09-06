"""Backup and rollback for the board database.

`sqlite3`'s online backup API is used rather than copying the file, because a
file copy taken while a write is in flight can capture a torn page set -- and a
backup you cannot restore is worse than no backup, since it is trusted.

The drill this supports, end to end: take a backup, keep working, discover the
work was wrong, restore, and confirm the database is exactly at the backed-up
state. `tests/storage/test_backup.py` runs precisely that.
"""

import shutil
import sqlite3
from pathlib import Path

from . import ids


def backup_database(conn, destination):
    """Take a consistent snapshot of an open database using the backup API."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(str(destination))
    try:
        conn.backup(target)
    finally:
        target.close()
    return destination


def restore_database(source, db_path, *, keep_replaced=True):
    """Restore a database from a snapshot.

    The file being replaced is preserved next to the target by default. Rolling
    back is itself a destructive act, and having the pre-rollback state on disk
    is what makes it reversible if the rollback was the mistake.
    """
    source = Path(source)
    db_path = Path(db_path)
    if not source.is_file():
        raise FileNotFoundError("No such backup: {}".format(source))
    replaced = None
    if db_path.exists() and keep_replaced:
        replaced = db_path.with_suffix(
            db_path.suffix + ".replaced-" + ids.now().replace(":", "").replace("-", "")
        )
        shutil.copy2(db_path, replaced)
    # Drop the WAL/SHM sidecars: leaving a WAL from the newer database beside a
    # restored file is how a "successful" restore silently reintroduces the
    # very writes it was supposed to undo.
    for sidecar in ("-wal", "-shm"):
        side = Path(str(db_path) + sidecar)
        if side.exists():
            side.unlink()
    shutil.copy2(source, db_path)
    return {"restored_from": str(source), "replaced_copy":
            str(replaced) if replaced else None}


def integrity_check(conn):
    """Return SQLite's own verdict on the file. 'ok' means structurally sound."""
    return conn.execute("PRAGMA integrity_check").fetchone()[0]
