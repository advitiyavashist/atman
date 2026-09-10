"""Regression for T-328 (T-299 verdict on T-266): the hook_events PK rebuild
must be atomic.

`_migrate_hook_events_pk` does the standard SQLite create-new/copy/drop-old
dance to rekey `hook_events`. The connection is opened with
`isolation_level=None` (autocommit), so before this fix each statement in the
rebuild committed on its own: a crash partway left the database with
`hook_events` either missing entirely or half-populated. Because
`apply_schema`'s `CREATE TABLE IF NOT EXISTS` silently recreates an empty
`hook_events` on the next boot, the old rows -- the audit trail -- become
permanently and silently unreachable, with no error raised anywhere.

These tests simulate the crash for real: a spawned child process (not
`fork` -- Python 3.9 + sqlite3 + pytest on macOS SIGSEGVs inside the forked
interpreter before the crash point) runs the actual `_migrate_hook_events_pk`
against a real sqlite file, intercepting the script it hands to
`executescript()` so it can stop and hard-kill itself (`os._exit`, not a
Python exception -- nothing in the production `except` block gets a chance
to run) right after a chosen statement lands. The parent then reopens the
same file through the normal `connect()` path -- the way a restarted process
actually would -- and checks whether the pre-crash row survived.

Run against the pre-T328 migrations.py (tickets main / T-266 pin bc12b35),
both tests below fail: a crash after the RENAME loses the row outright, and a
crash after the DROP leaves the row unreachable inside the leftover
`hook_events_pre_t266` table forever, even after a subsequent clean boot.
"""

import multiprocessing
import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

LEGACY_PROJECT_ID = "prj_legacy0001"
LEGACY_AGENT_ID = "agt_legacy0001"
LEGACY_EVENT_ID = "hev_legacy0001"


def _seed_pre_t266_board(db_path):
    """A board created before T-266: hook_events keyed on event_id alone."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE projects (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            version    INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            source     TEXT NOT NULL DEFAULT 'native'
                       CHECK (source IN ('native', 'imported_legacy')),
            paused     INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1))
        );
        CREATE TABLE hook_events (
            event_id    TEXT PRIMARY KEY,
            project_id  TEXT NOT NULL,
            agent_id    TEXT NOT NULL,
            session_id  TEXT NOT NULL,
            kind        TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            cwd         TEXT,
            note        TEXT
        );
    """)
    conn.execute(
        "INSERT INTO projects (id, name, created_at) VALUES (?, ?, ?)",
        (LEGACY_PROJECT_ID, "Legacy Board", "2026-01-01T00:00:00Z"))
    conn.execute(
        "INSERT INTO hook_events (event_id, project_id, agent_id, session_id,"
        " kind, occurred_at) VALUES (?, ?, ?, ?, ?, ?)",
        (LEGACY_EVENT_ID, LEGACY_PROJECT_ID, LEGACY_AGENT_ID, "ses_legacy0001",
         "session_start", "2026-01-01T00:00:00Z"))
    conn.commit()
    conn.close()


def _child_crash_after(db_path_str, stop_after_prefix):
    """Runs in a spawned child: drive the real migration, hard-kill right
    after the first statement matching `stop_after_prefix` lands.

    `sqlite3.Connection` instances refuse plain attribute assignment (its
    methods are read-only slots), so intercepting `executescript()` needs a
    real subclass via the `factory=` connect argument, not a monkeypatch.
    """
    import os

    class _SteppingConnection(sqlite3.Connection):
        def executescript(self, script):
            for stmt in (s.strip() for s in script.split(";")):
                if not stmt:
                    continue
                self.execute(stmt)
                if stmt.upper().startswith(stop_after_prefix):
                    os._exit(1)  # simulated crash: no commit, no rollback, no cleanup

    conn = sqlite3.connect(db_path_str, isolation_level=None,
                            factory=_SteppingConnection)
    conn.execute("PRAGMA foreign_keys = ON")
    from ticket_board.storage.migrations import _migrate_hook_events_pk
    _migrate_hook_events_pk(conn)
    os._exit(2)  # target statement never ran -- the test itself is wrong


def _crash_migration_after(db_path, stop_after_prefix):
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(target=_child_crash_after,
                        args=(str(db_path), stop_after_prefix))
    proc.start()
    proc.join(timeout=10)
    assert proc.exitcode == 1, (
        f"child did not reach the crash point (exitcode={proc.exitcode}); "
        "fix the test, this isn't testing what it claims to")


def _reopen_and_get_legacy_row(db_path):
    """The way a restarted process actually reopens the board."""
    from ticket_board.storage.db import connect
    conn = connect(db_path)
    try:
        return conn.execute(
            "SELECT project_id, agent_id, session_id, kind FROM hook_events"
            " WHERE event_id = ?", (LEGACY_EVENT_ID,)).fetchone()
    finally:
        conn.close()


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "board.sqlite3"
    _seed_pre_t266_board(path)
    return path


def test_crash_right_after_rename_does_not_lose_the_row(db_path):
    """The worst-case window: hook_events has just been renamed out of the
    way and the replacement doesn't exist yet. Pre-fix, a crash here means
    the next boot recreates an empty hook_events and the row is gone for
    good. Fixed, the rename never durably happened -- it rolls back."""
    _crash_migration_after(db_path, "ALTER TABLE")

    row = _reopen_and_get_legacy_row(db_path)
    assert row is not None, (
        "legacy hook_events row was lost after a crash right after the RENAME")
    assert tuple(row) == (LEGACY_PROJECT_ID, LEGACY_AGENT_ID, "ses_legacy0001",
                          "session_start")


def test_crash_right_after_create_does_not_lose_the_row(db_path):
    """The most severe window: the empty replacement table exists (with the
    new PK) but the copy from the renamed-away original hasn't run yet.
    Pre-fix, a crash here is worse than the RENAME case -- the next boot
    sees a new-shape hook_events and never re-migrates, so hook_events stays
    PERMANENTLY EMPTY and every historical row, not just this one, is
    orphaned inside the leftover hook_events_pre_t266 table forever."""
    _crash_migration_after(db_path, "CREATE TABLE")

    row = _reopen_and_get_legacy_row(db_path)
    assert row is not None, (
        "legacy hook_events row was lost after a crash right after CREATE TABLE")
    assert tuple(row) == (LEGACY_PROJECT_ID, LEGACY_AGENT_ID, "ses_legacy0001",
                          "session_start")
