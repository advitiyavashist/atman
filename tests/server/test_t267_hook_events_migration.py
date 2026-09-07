"""Regression for T-267: a PRIMARY KEY change is a migration.

T-266 rekeyed `hook_events` from `(event_id)` to `(project_id, event_id)` to
scope its dedup check by project. `CREATE TABLE IF NOT EXISTS` in schema.py
no-ops against a board that already has the table, so a board created before
T-266 kept the old single-column PK -- and sonnet-qa's T-267 review confirmed
that a genuine cross-project `event_id` collision on such a board then raised
`sqlite3.IntegrityError` instead of the pre-T-266 silent drop it was meant to
replace. This seeds an old-shape `hook_events` table, opens it through the
real `connect()` path, and proves the migration rebuilds it and both
projects' events land afterwards.
"""

import sqlite3
import uuid

import pytest

from api_client import Client, rid

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


def _pk_columns(conn, table):
    return [row[1] for row in conn.execute("PRAGMA table_info({})".format(table))
            if row[5]]


@pytest.fixture()
def db_path(tmp_path):
    """Override the shared fixture: pre-seed a pre-T-266 hook_events table."""
    path = tmp_path / "board.sqlite3"
    _seed_pre_t266_board(path)
    return path


def test_opening_a_pre_t266_board_rebuilds_the_pk_and_keeps_the_row(server):
    assert set(_pk_columns(server.store.conn, "hook_events")) == {"project_id",
                                                                   "event_id"}
    row = server.store.conn.execute(
        "SELECT project_id, agent_id, session_id, kind FROM hook_events"
        " WHERE event_id = ?", (LEGACY_EVENT_ID,)).fetchone()
    assert tuple(row) == (LEGACY_PROJECT_ID, LEGACY_AGENT_ID, "ses_legacy0001",
                          "session_start")


def test_a_pre_t266_board_no_longer_crashes_on_a_real_cross_project_collision(
        server, operator, project, enrolled):
    """The exact case T-267 found, replayed against a migrated board: two
    projects' agents post the same event_id and both writes must land."""
    other_project = server.store.create_project("Other")
    other_session = server.bootstrap_operator(other_project["id"])
    other_operator = Client(server, project_id=other_project["id"],
                            cookie=other_session["session_token"],
                            csrf=other_session["csrf_token"])
    created = other_operator.post("/enrollments", {
        "request_id": rid(), "agent_name": "backend-2", "role": "backend",
        "connection_mode": "managed"})
    assert created.status == 201, created.json()
    other_session_id = "ses_" + uuid.uuid4().hex[:8]
    exchanged = Client(server, project_id=other_project["id"]).post("/sessions", {
        "request_id": rid(), "code": created.json()["code"],
        "session_id": other_session_id})
    assert exchanged.status == 201, exchanged.json()
    other_payload = exchanged.json()
    other_agent_id = other_payload["agent"]["id"]
    other_client = Client(server, project_id=other_project["id"],
                          token=other_payload["token"], origin=None)

    shared_event_id = "hev_" + uuid.uuid4().hex
    first = enrolled["client"].post("/hook-events", {
        "request_id": rid(),
        "event": {"event_id": shared_event_id, "agent_id": enrolled["agent_id"],
                  "session_id": enrolled["session_id"], "kind": "session_start",
                  "occurred_at": "2026-09-07T09:00:00Z"}})
    assert first.status == 200 and first.json()["deduplicated"] is False

    second = other_client.post("/hook-events", {
        "request_id": rid(),
        "event": {"event_id": shared_event_id, "agent_id": other_agent_id,
                  "session_id": other_session_id, "kind": "session_start",
                  "occurred_at": "2026-09-07T09:00:00Z"}})
    assert second.status == 200, second.json()
    assert second.json()["deduplicated"] is False

    other_health = other_operator.get("/agents").json()["items"]
    other_agent = next(a for a in other_health if a["id"] == other_agent_id)
    assert other_agent["hook_health"]["server_received"] is True
    assert other_agent["last_heartbeat_at"] is not None

    rows = server.store.conn.execute(
        "SELECT COUNT(*) FROM hook_events WHERE event_id = ?",
        (shared_event_id,)).fetchone()
    assert rows[0] == 2, "one row per project, not deduplicated across them"
