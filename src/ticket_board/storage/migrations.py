"""Additive storage migrations layered on top of the T-179 base schema."""

SCHEMA_VERSION = 3

MESSAGING_DDL = """
CREATE TABLE IF NOT EXISTS members (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('human', 'agent', 'master')),
    display_name TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    state        TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'revoked')),
    agent_id     TEXT REFERENCES agents(id) ON DELETE SET NULL,
    availability TEXT NOT NULL DEFAULT 'unknown'
                 CHECK (availability IN ('available', 'busy', 'offline', 'unknown')),
    version      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_members_project ON members(project_id, state);
CREATE UNIQUE INDEX IF NOT EXISTS idx_members_project_agent
    ON members(project_id, agent_id) WHERE agent_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS invitations (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    role         TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    state        TEXT NOT NULL DEFAULT 'pending'
                 CHECK (state IN ('pending', 'accepted', 'expired', 'revoked')),
    created_by   TEXT,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    accepted_by  TEXT REFERENCES members(id) ON DELETE SET NULL,
    version      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_invitations_project ON invitations(project_id, state);

CREATE TABLE IF NOT EXISTS channels (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    kind              TEXT NOT NULL DEFAULT 'channel' CHECK (kind IN ('channel', 'dm')),
    visibility        TEXT NOT NULL CHECK (visibility IN ('public', 'private')),
    topic             TEXT,
    designated_master TEXT REFERENCES agents(id) ON DELETE SET NULL,
    version           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    UNIQUE (project_id, name)
);
CREATE INDEX IF NOT EXISTS idx_channels_project ON channels(project_id, visibility);

CREATE TABLE IF NOT EXISTS channel_members (
    project_id  TEXT NOT NULL,
    channel_id  TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    member_id   TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    subscribed  INTEGER NOT NULL DEFAULT 1 CHECK (subscribed IN (0, 1)),
    joined_at   TEXT NOT NULL,
    PRIMARY KEY (channel_id, member_id)
);
CREATE INDEX IF NOT EXISTS idx_channel_members_member
    ON channel_members(project_id, member_id);

CREATE TABLE IF NOT EXISTS messages (
    id                    TEXT PRIMARY KEY,
    project_id            TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    channel_id            TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    thread_id             TEXT,
    author                TEXT NOT NULL,
    body                  TEXT NOT NULL,
    intent                TEXT NOT NULL CHECK (intent IN ('message', 'task', 'reply', 'receipt')),
    ticket_id             TEXT,
    mentions              TEXT NOT NULL DEFAULT '[]',
    causation_id          TEXT REFERENCES messages(id) ON DELETE SET NULL,
    conversation_id       TEXT,
    supersedes_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
    version               INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_channel
    ON messages(project_id, channel_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_messages_thread
    ON messages(project_id, thread_id, created_at, id);

CREATE TABLE IF NOT EXISTS threads (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    channel_id      TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    root_message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    ticket_id       TEXT,
    reply_count     INTEGER NOT NULL DEFAULT 0 CHECK (reply_count >= 0),
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_threads_channel ON threads(project_id, channel_id);

CREATE TABLE IF NOT EXISTS deliveries (
    id                 TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    message_id         TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    recipient_agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    state              TEXT NOT NULL
                       CHECK (state IN ('sent', 'queued', 'delivered', 'started',
                                        'responded', 'blocked', 'awaiting_approval',
                                        'canceled', 'failed')),
    reason             TEXT CHECK (
                       reason IS NULL OR reason IN ('runner_offline',
                                                    'manual_resume_required',
                                                    'agent_busy',
                                                    'dependency_unmet',
                                                    'permission_required',
                                                    'budget_exceeded',
                                                    'project_paused',
                                                    'canceled_by_operator',
                                                    'dispatch_failed')),
    reason_detail      TEXT,
    run_id             TEXT,
    blocking_ticket_id TEXT,
    attempts           INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at    TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    version            INTEGER NOT NULL DEFAULT 1,
    UNIQUE (message_id, recipient_agent_id)
);
CREATE INDEX IF NOT EXISTS idx_deliveries_message ON deliveries(project_id, message_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_recipient
    ON deliveries(project_id, recipient_agent_id, state);

CREATE TABLE IF NOT EXISTS wake_jobs (
    id                 TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    message_id         TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    recipient_agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    delivery_id        TEXT NOT NULL REFERENCES deliveries(id) ON DELETE CASCADE,
    ticket_id          TEXT,
    state              TEXT NOT NULL DEFAULT 'pending'
                       CHECK (state IN ('pending', 'leased', 'completed', 'failed', 'canceled')),
    dedupe_key         TEXT NOT NULL,
    attempts           INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    lease_expires_at   TEXT,
    created_at         TEXT NOT NULL,
    UNIQUE (project_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_wake_jobs_recipient
    ON wake_jobs(project_id, recipient_agent_id, state, created_at);

CREATE TABLE IF NOT EXISTS runs (
    id                 TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    recipient_agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    session_id         TEXT,
    wake_job_id        TEXT REFERENCES wake_jobs(id) ON DELETE SET NULL,
    ticket_claim       TEXT,
    state              TEXT NOT NULL
                       CHECK (state IN ('pending', 'starting', 'running',
                                        'paused', 'responded', 'canceled', 'failed')),
    terminal_reason    TEXT,
    budget             TEXT NOT NULL,
    needs_approval     INTEGER NOT NULL DEFAULT 0 CHECK (needs_approval IN (0, 1)),
    started_at         TEXT,
    ended_at           TEXT,
    created_at         TEXT NOT NULL,
    version            INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_runs_recipient
    ON runs(project_id, recipient_agent_id, state);

CREATE TABLE IF NOT EXISTS runner_leases (
    agent_id             TEXT PRIMARY KEY REFERENCES agents(id) ON DELETE CASCADE,
    project_id           TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    runner_id            TEXT NOT NULL,
    epoch                INTEGER NOT NULL,
    acquired_at          TEXT NOT NULL,
    expires_at           TEXT NOT NULL,
    allowlisted_worktree TEXT NOT NULL,
    runtime_profile      TEXT NOT NULL,
    permission_policy    TEXT NOT NULL CHECK (permission_policy IN ('prompt', 'allowlist', 'deny_all')),
    concurrency          INTEGER NOT NULL DEFAULT 1 CHECK (concurrency = 1),
    budget               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runner_leases_project
    ON runner_leases(project_id, expires_at);

CREATE TRIGGER IF NOT EXISTS messages_body_immutable
BEFORE UPDATE OF body, author, channel_id, causation_id ON messages
BEGIN
    SELECT RAISE(ABORT, 'message bodies are immutable');
END;
"""


def _hook_events_keyed_on_event_id_alone(conn):
    """True for a board created before T-266, whose PK was `event_id` only.

    `CREATE TABLE IF NOT EXISTS` in schema.py no-ops against an existing
    table regardless of its shape, so an old board never picks up the T-266
    `(project_id, event_id)` key on its own -- this has to check and fix it.
    """
    columns = conn.execute("PRAGMA table_info(hook_events)").fetchall()
    pk_columns = [row[1] for row in columns if row[5]]
    return pk_columns == ["event_id"]


def _migrate_hook_events_pk(conn):
    """Rebuild `hook_events` keyed `(project_id, event_id)` (T-266).

    Existing rows cannot collide on the new key: the old single-column PK
    already forced every `event_id` to be globally unique, so this is a
    straight rebuild-and-copy, never a conflict to resolve. Without it, a
    board that predates T-266 keeps the old-shape table forever -- the
    dedup query is scoped by project, but a genuine cross-project
    `event_id` collision then hits the table's own PK and raises
    `IntegrityError` instead of the pre-T-266 silent drop it was meant to
    replace.
    """
    if not _hook_events_keyed_on_event_id_alone(conn):
        return
    conn.executescript("""
        ALTER TABLE hook_events RENAME TO hook_events_pre_t266;

        CREATE TABLE hook_events (
            event_id    TEXT NOT NULL,
            project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            agent_id    TEXT NOT NULL,
            session_id  TEXT NOT NULL,
            kind        TEXT NOT NULL
                        CHECK (kind IN ('session_start', 'user_prompt_submit',
                                        'stop', 'probe')),
            occurred_at TEXT NOT NULL,
            cwd         TEXT,
            note        TEXT,
            PRIMARY KEY (project_id, event_id)
        );

        INSERT INTO hook_events (event_id, project_id, agent_id, session_id,
                                  kind, occurred_at, cwd, note)
            SELECT event_id, project_id, agent_id, session_id,
                   kind, occurred_at, cwd, note
            FROM hook_events_pre_t266;

        DROP TABLE hook_events_pre_t266;

        CREATE INDEX IF NOT EXISTS idx_hook_events_agent
            ON hook_events(agent_id, occurred_at);
    """)


def apply_migrations(conn):
    """Apply migrations after the base schema exists."""
    conn.executescript(MESSAGING_DDL)
    _migrate_hook_events_pk(conn)
