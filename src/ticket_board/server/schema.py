"""Server-owned tables: credentials, enrollments and routing decisions.

These live in the same SQLite file as the T-179 board state but in a separate
DDL script, applied by the server rather than by `storage.schema`. Two reasons,
both practical:

* T-179's schema is the record layer for the whole epic and other lanes (T-202
  messaging) are writing to that file right now. Adding auth tables there would
  make every credential change a merge conflict with someone else's records.
* Nothing in the storage layer needs these tables. A board opened by the CLI,
  the importer or a backup drill has no credentials in it and should not.

Secrets are stored as SHA-256 hashes, never in the clear. An operator session
cookie, an agent token and an enrollment code are all bearer secrets: whoever
reads the row can act as the principal, so a database file or a backup copy
must not be enough to do it.
"""

DDL = """
-- Human principals. A full `Member` record (roles, invitations, channel
-- membership) is T-187; this is the minimum an operator session needs to name
-- its actor, and it uses the same `mem_` id shape so T-187 can adopt the rows.
CREATE TABLE IF NOT EXISTS operators (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'owner'
                 CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    created_at   TEXT NOT NULL
);

-- Dashboard sessions. `token_hash` is the primary key so a lookup is a hash
-- comparison and the raw cookie value exists only in the operator's browser.
CREATE TABLE IF NOT EXISTS operator_sessions (
    token_hash  TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL REFERENCES operators(id) ON DELETE CASCADE,
    project_id  TEXT NOT NULL,
    csrf_hash   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    revoked_at  TEXT
);

-- Project-scoped agent bearer tokens, one row per issued credential.
-- `project_id` is on the row rather than resolved through the agent, so a
-- scope check is one read and cannot be skipped by a join that returns NULL.
CREATE TABLE IF NOT EXISTS agent_tokens (
    token_hash TEXT PRIMARY KEY,
    agent_id   TEXT NOT NULL,
    project_id TEXT NOT NULL,
    session_id TEXT,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_tokens_agent ON agent_tokens(agent_id);

-- One-time enrollment codes. Single use is enforced by `used_at`, not by
-- deleting the row: an operator investigating a failed connect needs to see
-- that the code was already spent rather than that it never existed.
CREATE TABLE IF NOT EXISTS enrollments (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    agent_id   TEXT NOT NULL,
    code_hash  TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at    TEXT
);

-- Routing decisions shown on the master panel, including the deliberate
-- non-assignments. A board that only records what it DID assign cannot answer
-- "why is DEMO-15 still sitting there", which is the question the panel exists
-- to answer. The sweep that writes most of these is T-182; the table and the
-- read path are here because `MasterPanelResponse.decisions` is a frozen field.
CREATE TABLE IF NOT EXISTS master_decisions (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    ticket_id  TEXT NOT NULL,
    decision   TEXT NOT NULL
               CHECK (decision IN ('assigned', 'skipped', 'split_recommended',
                                   'no_eligible_agent')),
    reason     TEXT NOT NULL,
    agent_id   TEXT,
    decided_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_master_decisions
    ON master_decisions(project_id, seq DESC);
"""


def apply_server_schema(conn):
    """Create the server-owned tables. Safe to run on an open database."""
    conn.executescript(DDL)
