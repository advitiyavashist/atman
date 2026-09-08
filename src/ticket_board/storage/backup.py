"""Backup and rollback for the board database.

`sqlite3`'s online backup API is used rather than copying the file, because a
file copy taken while a write is in flight can capture a torn page set -- and a
backup you cannot restore is worse than no backup, since it is trusted.

The drill this supports, end to end: take a backup, keep working, discover the
work was wrong, restore, and confirm the database is exactly at the backed-up
state. `tests/storage/test_backup_rollback.py` runs precisely that.
"""

import os
import shutil
import sqlite3
import tempfile
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


def _clear_sidecars(path):
    for sidecar in ("-wal", "-shm"):
        side = Path(str(path) + sidecar)
        if side.exists():
            side.unlink()


def _integrity_of(path):
    """SQLite's verdict on a database file.

    Opened read-write on purpose. A snapshot taken with the backup API inherits
    the source's WAL journal mode, and a WAL database cannot be opened read-only
    unless its `-shm` already exists -- so the obvious `mode=ro` spelling fails
    on exactly the files this project produces. Callers hand this a copy they
    own, never the operator's original.
    """
    try:
        conn = sqlite3.connect(str(path))
    except sqlite3.DatabaseError as exc:
        return "unopenable: {}".format(exc)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        # Badly enough damaged that the check cannot run is still an answer,
        # and the caller needs a verdict rather than an exception it might not
        # be expecting on a health query.
        return "unreadable: {}".format(exc)
    finally:
        conn.close()
        _clear_sidecars(path)


def check_snapshot(source):
    """Is this backup restorable? Answered without touching the backup.

    The check runs against a temporary copy, because verifying a file means
    opening it, and opening the operator's only good copy read-write to find out
    whether it is good is not a trade worth making.
    """
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError("No such backup: {}".format(source))
    scratch = Path(tempfile.mkdtemp(prefix="board-check-"))
    try:
        probe = scratch / "snapshot.sqlite3"
        shutil.copy2(source, probe)
        return _integrity_of(probe)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def restore_database(source, db_path, *, keep_replaced=True, verify=True):
    """Restore a database from a snapshot.

    The file being replaced is preserved next to the target by default. Rolling
    back is itself a destructive act, and having the pre-rollback state on disk
    is what makes it reversible if the rollback was the mistake.

    Two things this refuses to do, both learned the same way -- a restore is
    run by someone who has already lost something:

    - **It checks the snapshot before touching the target.** Restoring a
      corrupt backup over a working database destroys the last good copy in
      order to install an unusable one. `verify=False` exists for the operator
      who has nothing else left and wants the bytes anyway.
    - **It swaps atomically.** The old code copied the snapshot over the live
      file in place, so an interruption partway through left a file that is
      neither the backup nor the database. The snapshot is staged beside the
      target and renamed onto it, which on a single filesystem is atomic: a
      reader sees the old file or the new one, never half of each.
    """
    source = Path(source)
    db_path = Path(db_path)
    if not source.is_file():
        raise FileNotFoundError("No such backup: {}".format(source))
    # Stage next to the target so the rename is same-filesystem, hence atomic.
    staged = Path(str(db_path) + ".restoring")
    shutil.copy2(source, staged)
    try:
        if verify:
            verdict = _integrity_of(staged)
            if verdict != "ok":
                raise sqlite3.DatabaseError(
                    "Refusing to restore a snapshot that fails integrity_check:"
                    " {} said {!r}".format(source, verdict)
                )
    except BaseException:
        staged.unlink()
        raise

    replaced = None
    if db_path.exists() and keep_replaced:
        replaced = db_path.with_suffix(
            db_path.suffix + ".replaced-" + ids.now().replace(":", "").replace("-", "")
        )
        shutil.copy2(db_path, replaced)
    # Drop the WAL/SHM sidecars: leaving a WAL from the newer database beside a
    # restored file is how a "successful" restore silently reintroduces the
    # very writes it was supposed to undo. They go *before* the rename, so the
    # moment the new file appears its sidecars are already gone.
    _clear_sidecars(db_path)
    os.replace(staged, db_path)
    return {"restored_from": str(source), "replaced_copy":
            str(replaced) if replaced else None,
            "verified": bool(verify)}


def integrity_check(conn):
    """Return SQLite's own verdict on the file. 'ok' means structurally sound."""
    return conn.execute("PRAGMA integrity_check").fetchone()[0]
