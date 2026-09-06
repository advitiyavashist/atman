"""The backup and rollback drill.

A backup that has never been restored is a guess. These tests run the whole
drill: snapshot, keep working, decide the work was wrong, restore, and verify
the database is exactly at the snapshot -- including that the WAL from the
newer state does not leak the undone writes back in.
"""

import shutil
from pathlib import Path

import pytest

from ticket_board.storage import BoardStore, NotFound
from ticket_board.storage.backup import (
    backup_database,
    integrity_check,
    restore_database,
)


def test_backup_produces_a_readable_database(store, project, tmp_path):
    store.create_ticket(project["id"], "BAK-1", "Before the snapshot")
    snapshot = backup_database(store.conn, tmp_path / "snap" / "board.bak")
    assert snapshot.is_file()

    restored = BoardStore(snapshot)
    try:
        assert integrity_check(restored.conn) == "ok"
        assert [t["id"] for t in restored.list_tickets(project["id"])] == ["BAK-1"]
    finally:
        restored.close()


def test_full_rollback_drill(db_path, tmp_path):
    """Snapshot, do damage, roll back, confirm the damage is gone."""
    store = BoardStore(db_path)
    project = store.create_project("Drill")
    agent = store.create_agent(project["id"], "backend-1")
    good = store.create_ticket(project["id"], "DRILL-1", "Known good")
    store.claim_ticket(project["id"], "DRILL-1", agent["id"],
                       expected_version=good["version"])
    good_state = store.get_ticket(project["id"], "DRILL-1")
    audit_before = len(store.audit_trail(project["id"], limit=10000))

    snapshot = backup_database(store.conn, tmp_path / "board.bak")

    # Work continues, and turns out to be wrong.
    store.create_ticket(project["id"], "DRILL-2", "Regrettable")
    store.create_ticket(project["id"], "DRILL-3", "Also regrettable")
    store.transition(project["id"], "DRILL-1", "open",
                     expected_version=good_state["version"])
    assert len(store.list_tickets(project["id"])) == 3
    store.close()

    result = restore_database(snapshot, db_path)
    assert result["replaced_copy"], "the pre-rollback state is preserved"
    assert Path(result["replaced_copy"]).is_file()

    store = BoardStore(db_path)
    try:
        assert integrity_check(store.conn) == "ok"
        tickets = store.list_tickets(project["id"])
        assert [t["id"] for t in tickets] == ["DRILL-1"], \
            "the regrettable tickets must be gone"
        assert store.get_ticket(project["id"], "DRILL-1") == good_state, \
            "the surviving ticket must be byte-for-byte at the snapshot"
        assert len(store.audit_trail(project["id"], limit=10000)) == audit_before
        with pytest.raises(NotFound):
            store.get_ticket(project["id"], "DRILL-2")
    finally:
        store.close()


def test_restore_does_not_resurrect_writes_through_a_stale_wal(db_path, tmp_path):
    """The failure mode a naive file-copy restore hits after a crash.

    A *clean* shutdown checkpoints the WAL into the main file and deletes it,
    so copying a snapshot over the db is fine -- verified, and the reason this
    test stages a crash instead. After an unclean exit the WAL survives holding
    the post-snapshot writes, and copying the snapshot over the main file alone
    lets SQLite replay them: the restore silently does nothing. Both halves are
    asserted below so the contrast is the test, not a comment.
    """
    store = BoardStore(db_path)
    project = store.create_project("Wal")
    store.create_ticket(project["id"], "WAL-1", "Before")
    snapshot = backup_database(store.conn, tmp_path / "wal.bak")
    store.create_ticket(project["id"], "WAL-2", "After the snapshot")

    # Stage a crash: capture the db and its sidecars exactly as they sit on
    # disk with the connection still open, so the WAL still holds WAL-2.
    wal = Path(str(db_path) + "-wal")
    assert wal.exists() and wal.stat().st_size > 0, "expected a populated WAL"

    def _crash_copy(destination):
        destination.mkdir()
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(db_path) + suffix)
            if source.exists():
                shutil.copy2(source, destination / ("board.sqlite3" + suffix))
        return destination / "board.sqlite3"

    naive_db = _crash_copy(tmp_path / "crash_naive")
    safe_db = _crash_copy(tmp_path / "crash_safe")
    store.close()

    # Naive restore: overwrite the main file, leave the sidecars.
    shutil.copy2(snapshot, naive_db)
    naive = BoardStore(naive_db)
    try:
        resurrected = [t["id"] for t in naive.list_tickets(project["id"])]
    finally:
        naive.close()
    assert resurrected == ["WAL-1", "WAL-2"], (
        "expected the naive restore to replay the stale WAL; if this ever "
        "stops happening the test below no longer proves anything"
    )

    # Our restore drops the sidecars first.
    restore_database(snapshot, safe_db)
    assert not Path(str(safe_db) + "-wal").exists(), "stale WAL must be removed"
    safe = BoardStore(safe_db)
    try:
        assert [t["id"] for t in safe.list_tickets(project["id"])] == ["WAL-1"], \
            "WAL-2 came back: the restore was undone by a stale WAL"
    finally:
        safe.close()


def test_restoring_a_missing_snapshot_fails_loudly(db_path, tmp_path):
    with pytest.raises(FileNotFoundError):
        restore_database(tmp_path / "nope.bak", db_path)


def test_backup_is_consistent_under_a_concurrent_writer(db_path, tmp_path):
    """The reason for the backup API rather than shutil.copy.

    A snapshot taken while another connection is mid-transaction must still be
    structurally sound and must not contain the uncommitted work.
    """
    store = BoardStore(db_path)
    project = store.create_project("Concurrent")
    store.create_ticket(project["id"], "CONC-1", "Committed")

    other = BoardStore(db_path)
    other.conn.execute("BEGIN IMMEDIATE")
    other.conn.execute(
        "INSERT INTO tickets (id, project_id, title, acceptance, state, version,"
        " files, created_at, updated_at) VALUES"
        " ('CONC-2', ?, 'Uncommitted', '[]', 'open', 1, '[]', ?, ?)",
        (project["id"], "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    snapshot = backup_database(store.conn, tmp_path / "concurrent.bak")
    other.conn.execute("ROLLBACK")
    other.close()
    store.close()

    restored = BoardStore(snapshot)
    try:
        assert integrity_check(restored.conn) == "ok"
        assert [t["id"] for t in restored.list_tickets(project["id"])] == ["CONC-1"]
    finally:
        restored.close()
