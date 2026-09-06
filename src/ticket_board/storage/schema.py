"""SQLite schema for the board store.

Two things here are load-bearing and easy to lose in a later refactor:

1. `audit_events` is append-only, enforced by triggers rather than by
   convention. An audit trail that the application layer *promises* not to
   rewrite is not an audit trail; a reviewer has to be able to say the record
   could not have been edited after the fact.
2. Ticket state is `open|claimed|review|done|blocked` and nothing else --
   `dependency_blocked` is derived at read time, per the T-178 freeze. The
   CHECK constraint is what stops a future writer from persisting it as a
   sixth state.
"""

SCHEMA_VERSION = 1

DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    version    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'native'
               CHECK (source IN ('native', 'imported_legacy')),
    paused     INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1))
);

CREATE TABLE IF NOT EXISTS agents (
    id                 TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name               TEXT NOT NULL,
    role               TEXT,
    capabilities       TEXT NOT NULL DEFAULT '[]',
    state              TEXT NOT NULL DEFAULT 'idle'
                       CHECK (state IN ('connected', 'idle', 'working',
                                        'awaiting-input', 'offline', 'revoked')),
    connection_mode    TEXT NOT NULL DEFAULT 'hook_only'
                       CHECK (connection_mode IN ('managed', 'hook_only')),
    runtime            TEXT NOT NULL DEFAULT '{}',
    max_active_tickets INTEGER NOT NULL DEFAULT 1
                       CHECK (max_active_tickets >= 1),
    current_ticket     TEXT,
    worktree           TEXT,
    hook_health        TEXT NOT NULL DEFAULT '{}',
    last_heartbeat_at  TEXT,
    last_progress_at   TEXT,
    version            INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT NOT NULL,
    UNIQUE (project_id, name)
);

CREATE TABLE IF NOT EXISTS session_leases (
    session_id      TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    acquired_at     TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    revoked_at      TEXT,
    revocation_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_session_leases_agent ON session_leases(agent_id);

CREATE TABLE IF NOT EXISTS tickets (
    id               TEXT NOT NULL,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title            TEXT NOT NULL,
    outcome          TEXT,
    acceptance       TEXT NOT NULL DEFAULT '[]',
    -- No 'dependency-blocked' member: it is derived. See T-178 freeze.
    state            TEXT NOT NULL DEFAULT 'open'
                     CHECK (state IN ('open', 'claimed', 'review', 'done', 'blocked')),
    version          INTEGER NOT NULL DEFAULT 1,
    role             TEXT,
    owner            TEXT REFERENCES agents(id) ON DELETE SET NULL,
    owner_session    TEXT,
    blocked_reason   TEXT,
    files            TEXT NOT NULL DEFAULT '[]',
    worktree         TEXT,
    evidence         TEXT,
    next_step        TEXT,
    handoff          TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    claimed_at       TEXT,
    last_progress_at TEXT,
    PRIMARY KEY (project_id, id)
);
CREATE INDEX IF NOT EXISTS idx_tickets_state ON tickets(project_id, state);
CREATE INDEX IF NOT EXISTS idx_tickets_owner ON tickets(owner);

CREATE TABLE IF NOT EXISTS ticket_dependencies (
    project_id  TEXT NOT NULL,
    ticket_id   TEXT NOT NULL,
    depends_on  TEXT NOT NULL,
    PRIMARY KEY (project_id, ticket_id, depends_on),
    FOREIGN KEY (project_id, ticket_id) REFERENCES tickets(project_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_deps_depends_on ON ticket_dependencies(project_id, depends_on);

CREATE TABLE IF NOT EXISTS ticket_updates (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    ticket_id  TEXT NOT NULL,
    author     TEXT NOT NULL,
    body       TEXT NOT NULL,
    next_step  TEXT,
    created_at TEXT NOT NULL,
    -- Late updates from a superseded session are KEPT, flagged, and denied any
    -- effect on ticket state. Dropping them loses the only record of what the
    -- displaced session believed.
    superseded INTEGER NOT NULL DEFAULT 0 CHECK (superseded IN (0, 1)),
    FOREIGN KEY (project_id, ticket_id) REFERENCES tickets(project_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_updates_ticket ON ticket_updates(project_id, ticket_id, created_at);

CREATE TABLE IF NOT EXISTS reviews (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL,
    ticket_id      TEXT NOT NULL,
    state          TEXT NOT NULL DEFAULT 'requested'
                   CHECK (state IN ('requested', 'accepted', 'rejected')),
    submitted_by   TEXT NOT NULL,
    submitted_at   TEXT NOT NULL,
    evidence       TEXT NOT NULL,
    notes          TEXT,
    decided_by     TEXT,
    decided_at     TEXT,
    decision_notes TEXT,
    FOREIGN KEY (project_id, ticket_id) REFERENCES tickets(project_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_reviews_ticket ON reviews(project_id, ticket_id);

CREATE TABLE IF NOT EXISTS assignments (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    ticket_id   TEXT NOT NULL,
    agent_id    TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    state       TEXT NOT NULL DEFAULT 'queued'
                CHECK (state IN ('queued', 'claimed', 'expired', 'withdrawn')),
    reason      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    lease_epoch INTEGER NOT NULL DEFAULT 0,
    version     INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (project_id, ticket_id) REFERENCES tickets(project_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_assignments_agent ON assignments(agent_id, state);

CREATE TABLE IF NOT EXISTS master_lease (
    project_id             TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    holder                 TEXT,
    epoch                  INTEGER NOT NULL DEFAULT 0,
    acquired_at            TEXT NOT NULL,
    expires_at             TEXT NOT NULL,
    routing_mode           TEXT NOT NULL DEFAULT 'deterministic'
                           CHECK (routing_mode IN ('deterministic',
                                                   'deterministic_plus_suggestions')),
    paused                 INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1)),
    last_sweep_at          TEXT,
    next_sweep_at          TEXT,
    sweep_interval_seconds INTEGER NOT NULL DEFAULT 30
);

CREATE TABLE IF NOT EXISTS hook_events (
    event_id    TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_id    TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    kind        TEXT NOT NULL
                CHECK (kind IN ('session_start', 'user_prompt_submit', 'stop', 'probe')),
    occurred_at TEXT NOT NULL,
    cwd         TEXT,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_hook_events_agent ON hook_events(agent_id, occurred_at);

CREATE TABLE IF NOT EXISTS audit_events (
    id           TEXT PRIMARY KEY,
    seq          INTEGER NOT NULL,
    event_id     TEXT NOT NULL UNIQUE,
    project_id   TEXT NOT NULL,
    actor        TEXT NOT NULL,
    action       TEXT NOT NULL,
    subject_type TEXT,
    subject_id   TEXT,
    request_id   TEXT,
    occurred_at  TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_seq ON audit_events(project_id, seq);

-- Append-only, enforced by the database. The application layer cannot quietly
-- rewrite history, and a reviewer does not have to take our word for it.
CREATE TRIGGER IF NOT EXISTS audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;

-- request_id idempotency. Same id + identical body replays the stored result;
-- same id + different body is 409 request_id_reused.
CREATE TABLE IF NOT EXISTS request_log (
    request_id   TEXT NOT NULL,
    project_id   TEXT NOT NULL,
    operation    TEXT NOT NULL,
    body_hash    TEXT NOT NULL,
    response     TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (project_id, request_id)
);
"""


def apply_schema(conn):
    """Create the schema and stamp its version. Safe to run on an open db."""
    conn.executescript(DDL)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )


def schema_version(conn):
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else None
