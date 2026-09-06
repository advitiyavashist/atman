# Ticket Board — V1 design

Status: implementation specification, 2026-09-06. Owner: codex-master for design;
current delivery coordinator: cursor. This does not transfer board leadership.
Repository: advitiyavashist/tickets. Delivery tracking: Steer's existing board,
epic E-010. Build in separate worktrees of tickets; never in Steer's product UI.

## Product promise

Connect your agents. Give them work. See what needs you.

The first user runs several Claude Code sessions and loses track of assignments,
blocked work, finished branches, and agents that stopped responding. V1 connects
existing sessions, gives one master a durable queue, and exposes evidence of
progress. Claude Code is the first supported adapter. Other Claude runtimes can
implement the same protocol; do not advertise universal automatic compatibility.

## Scope and milestones

1. Local pilot: one operator, one board server, multiple projects and Claude Code
   sessions on the same machine. Import one existing board without destroying it.
2. Complete loop: connect → verify hook delivery → create task graph → master
   assigns → worker claims → updates → review with commit evidence → completion.
3. Recovery drill: worker disappears, alert appears, operator preserves its work
   and explicitly reassigns; replacement master resumes from durable state.

Remote agents use the same HTTP contract later through an authenticated TLS
endpoint. Hosting, billing, SSO, arbitrary terminal control, automatic agent
launching, and automatic PR merges are outside this first release. A hook does
not wake an idle model: queued work is delivered on its next hook/poll. Show that
state honestly. An optional supervised runner can be a later adapter.

## Interface and taste

Working name: **Ticket Board**. Quiet charcoal surfaces, off-white text, blue for
the primary action, amber for attention, green only for verified success.
Readable sans-serif text; monospace IDs and commit hashes. Compact and expressive,
without simulated terminal noise, sci-fi labels, motivational filler, or fake
completion percentages. Light mode is first-class. Use text with status colors.

Four navigation items: Overview, Tickets, Agents, Activity. Project switcher and
Connect agent remain visible. Settings live beneath the project switcher.

| Screen | Content | Primary action |
|---|---|---|
| Overview | Ready, in progress, awaiting review, blocked counts; attention queue; recent accepted work; master status | Review next item |
| Tickets | Search/filter; table default, optional state columns; dependency links | New ticket |
| Ticket detail | Outcome, acceptance checklist, owner, dependencies, files, progress notes, branch/SHA/PR and checks | Assign / request review / accept, by role |
| Agents | Durable ID, session ID, role, capabilities, current ticket, heartbeat and progress timestamps, hook health | Connect agent |
| Activity | Chronological assignments, status changes, messages, review decisions and recovery events | Filter or reply |
| Master panel | Current holder/lease, routing mode, last sweep, next sweep, queue, decision reasons, pause | Run check / pause assignments |

Empty Overview: “No work yet. Create a ticket or import a board.” No agent:
“Connect your first agent.” Stale stream: “Connection lost. Showing updates from
14:32.” Worker silence: “No heartbeat for 5m. Check session.” Agent Stop is
“Turn finished”, never “Ticket complete”. A pending assignment reads “Queued —
waiting for agent”, not “Working”. All durations use server timestamps.

Keyboard: semantic tables/buttons, visible focus, labels, dialog focus trap and
Escape dismissal, no color-only signals. Mobile becomes stacked rows. Respect
reduced motion; no chart unless its numerator, denominator and time window exist.
The companion prototype.html is a clickable fixture, not a running control plane.

## Connect an agent

1. Select project, choose Claude Code, give the agent a durable name and role,
   choose capabilities and worktree. Default concurrency is one active ticket.
2. Generate a single-use enrollment code, expires after 10 minutes. Dashboard
   displays the proposed install command and the configuration changes.
3. Agent runs `tickets connect` from its worktree (proposed command). Prompt for
   enrollment code through stdin, not shell history. Store project-scoped agent
   credentials in a 0600 local file, outside Git. Merge only owned hook entries;
   preserve unrelated entries. Provide inspect, diagnose, revoke and uninstall.
4. Test event travels adapter → server → context response. Show separate checks
   for installed config, server receipt, response and session adoption. A synthetic
   event alone does not prove the actual Claude session adopted the hook.
5. Connected view shows capability/version support and next step: open/resume a
   session, then claim work. Each runtime session has a unique ID beneath the
   stable agent identity. Simultaneous sessions cannot share a worker lease.

Claude supports lifecycle hooks and context injection at SessionStart and
UserPromptSubmit. Use a command adapter for these events; use supported tool/stop
events only for bounded metadata activity. Verify installed Claude version and
event schemas, preserve stop-loop guards, and report unsupported capabilities.
Sources: https://code.claude.com/docs/en/hooks and
https://code.claude.com/docs/en/hooks-guide (checked 2026-09-06).

Adapter failure is non-blocking for ordinary model work but cannot grant an
offline claim. Queue bounded metadata for retry, deduplicate by event_id, and
surface delivery failures. Never send raw prompts, tool arguments, transcripts,
environment values or credentials by default. User notes are explicit input.

## State and ownership

Ticket state: open → claimed → review → done. blocked is an explicit state;
dependency-blocked is derived. Rejection returns review to claimed with notes.
Assignment is a reservation separate from a claim; reservations expire. Done
requires reviewer acceptance of the exact submitted artifact. Git integration
records SHA and checks; a changing branch tip cannot replace reviewed evidence.

Agent state: connected/idle/working/awaiting-input/offline/revoked, supported by
timestamps and signals. Track heartbeat separately from meaningful progress.
The local connector sends a 30s heartbeat while its supervised session is alive;
90s stale, 5m offline are pilot defaults. Report hook-only sessions as activity
observed with liveness unknown between events. Never declare death from silence.

Master is a role lease, not a model name. Only one valid holder per board. Server
enforces expiry and fencing epoch on every routing mutation; old holders receive
409. Default sweep every 30s, pauseable. Deterministic eligibility (dependencies,
capabilities, free capacity, file/worktree conflicts) precedes model suggestions.
Master can recommend splits, assign eligible work and request progress. It cannot
approve its own implementation or execute arbitrary shell text from a ticket.

Persist task outcome, acceptance, dependencies, owner and session lease, next
step, handoff, worktree, branch/SHA, review evidence and timestamps. Recovery
requires a human/master check of artifacts, explicit lease revocation, recovery
note, then a new claim. Late updates from old sessions remain audit evidence and
cannot overwrite the new owner's state. Master takeover loads the checkpoint,
unread messages and review queue; alerts have an acknowledgement owner.

## Architecture and API contract to implement

One Python service owns validated mutations. React/TypeScript UI reads snapshots
and SSE events. Claude command adapter and CLI call the same service. Retain the
existing CLI's commands/board discovery through a compatibility layer. Adopt
SQLite transactions for managed boards; explicitly migrate legacy files after
backup and validation. Avoid concurrent JSON and SQLite writers: reject legacy
direct writes while server ownership is active. Imported counts and dependency
edges must match; retain original files and documented rollback.

Core records: Project, Ticket, Agent, SessionLease, Assignment, MasterLease,
HookEvent, Message, Review, AuditEvent. Every mutation includes actor, request_id,
expected_version; claims/assignments additionally require lease epoch. Responses
use 409 for conflicts and 422 for invalid state/dependency cycles.

| Proposed route | Contract |
|---|---|
| GET /api/v1/overview | Counts + attention items + master lease + snapshot version |
| GET/POST /api/v1/tickets | Filtered cursor page / validated outcome+acceptance+deps |
| POST /api/v1/tickets/{id}/claim | Atomic capacity/dependency/lease check |
| POST /api/v1/tickets/{id}/updates | Explicit progress + next step |
| POST /api/v1/tickets/{id}/reviews | Pinned evidence; reviewer decides separately |
| POST /api/v1/enrollments | Operator-scoped one-time code |
| POST /api/v1/sessions | Exchange code for scoped session credentials |
| POST /api/v1/hook-events | Deduplicated metadata; bounded context response |
| POST /api/v1/master/lease | Compare-and-swap takeover, fencing epoch |
| POST /api/v1/assignments | Master-scoped reservation with reason and expiry |
| GET /api/v1/events | SSE cursor, replay after Last-Event-ID; snapshot on gap |

All routes scope to a project via authenticated credentials. Bind loopback by
default. Dashboard uses an HttpOnly SameSite operator session with CSRF/origin
checks; agent tokens cannot create enrollments, accept reviews or take master
authority. No secret in URLs, browser localStorage, logs or fixture data. Remote
mode requires TLS and explicit operator configuration before exposure.

## Release acceptance

Two Claude sessions connect within five minutes from documented commands and
each gets a different eligible ticket. Racing eight claims yields one owner per
ticket; capacity remains one. Same event retried creates one audit record.
Two masters racing yield one current lease. Expired worker/master writes fail.
Dashboard updates within two seconds locally; reconnect has no lost final state.
Hook failure is visible, ordinary sessions continue, and no work is claimed
offline. Review shows pinned SHA; failed checks cannot show accepted. Worker and
master restart drills preserve handoff, branch evidence and assignment ownership.
Keyboard-only connection and review work. Legacy import/rollback has fixture
evidence. No real keys or live board modifications in acceptance tests.

## Delivery roles

cursor coordinates reviews and queue timing. claude-opus owns service/contract;
claude-fable owns master routing; claude-sonnet owns Claude onboarding/docs;
codex owns dashboard; cursor-2 owns integration/recovery QA. These are queued
lane assignments, not claims on busy workers. Each claims one task after its
dependencies and current commitment are complete. File boundaries and concrete
acceptance live in implementation-plan.json and the board ticket bodies.
