"""Connection and transaction handling.

The whole concurrency story rests on two choices here:

- **WAL** so a reader never blocks the writer. The dashboard polls constantly;
  without WAL every poll contends with the claim path.
- **`BEGIN IMMEDIATE`** for writes. Worth being precise about what this buys,
  because it is easy to overclaim: SQLite's snapshot isolation already stops
  two racing claims from both committing -- a deferred transaction whose
  snapshot predates the winner's commit fails its upgrade rather than
  overwriting. What deferred does *not* give you is a usable failure. Measured
  on this schema with eight processes on one ticket, the deferred loser
  surfaces `OperationalError: database is locked` instead of a clean
  `ticket_version_conflict`, so the caller gets an opaque driver error where
  the contract promises a 409 it can act on. Taking the write lock up front
  makes the check-then-write sequence read committed state, so every loser
  fails deterministically with a contract error code.
"""

import contextlib
import sqlite3

from .schema import apply_schema

# Wait rather than fail immediately when another process holds the write lock.
BUSY_TIMEOUT_MS = 5000


def connect(path, *, timeout=10.0):
    """Open a board database, creating and migrating it if needed."""
    conn = sqlite3.connect(str(path), timeout=timeout, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    apply_schema(conn)
    return conn


@contextlib.contextmanager
def write_txn(conn):
    """An exclusive-from-the-start write transaction.

    Rolls back on any exception, so a failed dependency-cycle check or capacity
    check leaves nothing behind -- the contract requires a 422 cycle to create
    nothing at all.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


@contextlib.contextmanager
def read_txn(conn):
    """A consistent read snapshot."""
    conn.execute("BEGIN DEFERRED")
    try:
        yield conn
    finally:
        conn.execute("ROLLBACK")
