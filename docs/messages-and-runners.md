# Messages that move work

V1 scope amendment, requested by user 2026-09-06. This replaces the earlier
deferral of automatic launching. The local `tickets ui` + `tickets watch` path
is implemented. The managed server runner described later in this document is
still a contract rather than a deployed service.

## Local V1 launch path

Every seat has a durable `wake_mode` in `workforce.json`:

- `task-only` (ordinary worker default): only an explicit task, held/assigned
  work, or a blocker starts a paid turn. Ordinary DMs remain notifications.
- `continuous` (current master and CoS default): a direct DM or named @mention
  also wakes the persistent adapter. ACK, self, review-copy and broadcast mail
  do not create reply loops.
- `scheduled`: the task-only gates with a persistent adapter. The mode itself
  creates no clock or deadline; configure an objective `--heartbeat` separately
  or let an external scheduler poll it.

Set it with `tickets join <seat> --wake-mode ...` or `tickets spawn <seat>
--wake-mode ...`. The setting is independent of harness: Claude Code, Codex,
Cursor and a custom/remote bridge use the same message rules. Existing boards
migrate without a rewrite: missing master/CoS values resolve to `continuous`;
other missing values resolve to `task-only`.

The dashboard sends a same-origin JSON message. A durable task wakes every
mode; a directed message wakes a continuous recipient. One watcher lease starts
one bounded harness run, which reads `tickets inbox`, claims or continues its
assigned ticket, and posts an acknowledgement or action. A message carrying
both `--to seat` and `@seat` is one record and therefore one wake. Reading the
inbox moves the dashboard receipt from pending to acknowledged.

The launch acceptance test runs this whole path on an isolated board with a
deterministic harness: task POST, actionable wake, exactly one run, bound ticket
update, reply, and both acknowledgement transitions. V1 local messaging is
agent-addressed; named channels remain part of the managed-service contract.

## What happens when you send

- DM to an agent: creates a durable delivery. It wakes a `continuous` adapter;
  use Send task / `--task` for a `task-only` or `scheduled` recipient. Task
  instructions are linked to an existing ticket or create a draft for routing.
- A channel mention wakes the named agent. An unaddressed task in a project
  channel wakes its designated master to decide the owner. Ordinary channel
  conversation reaches subscribed members without launching every agent.
- “Send task” requires outcome, assignee (or Route via master), and linked/new
  ticket. “Send” supports questions and follow-ups; the agent can respond without
  creating a new implementation claim. No rigid command syntax for users.
- Busy agent: message joins its existing run inbox, delivered at the next safe
  hook boundary or as the next turn. New tasks queue behind its current claim.
  Unfinished dependencies show “Waiting on DEMO-13”; never pretend work started.
- Offline runner: “Queued — runner offline”, retaining delivery until reconnection.
  Hook-only connection: “Manual resume required”, with a Connect runner action.

Receipt chain: Sent → Queued → Delivered to runner → Agent started → Responded.
Blocked, waiting for approval, canceled and failed are visible alternatives.
“Started” requires runtime session initialization plus valid task claim when it
is an execution request. Delivery is not acknowledgement; process spawn is not
proof of execution. Ticket completion still requires review.

## Runner contract

Operator enables managed mode when connecting the agent. Persist allowlisted
project/worktree, runtime profile, permission policy, concurrency=1, turn/time
budget, session ID and runner lease. The local supervisor persists independently
of a model turn; its authenticated subscription consumes durable wake jobs.
Use supported Claude programmatic mode and explicit session IDs to start/resume.
Never resume a session simultaneously owned by an interactive terminal. Adopt
only after explicit handoff, otherwise create a separate managed session/worktree.

Claude documents programmatic invocation and session resume at
https://code.claude.com/docs/en/headless and flags at
https://code.claude.com/docs/en/cli-reference (checked 2026-09-06). Implementers
must validate installed version and credential support. Pass message bodies via
stdin/structured input, never interpolate them into shell commands. Preserve
runtime permission controls; permission requests surface “Needs approval”.

Persist message and outbox record in one transaction. Jobs are at-least-once;
deduplicate on (message_id, recipient_agent_id). Session lease/fencing prevents
parallel starts; retain run ID before launching, reconcile existing local process
after crash before retrying, and treat uncertain external side effects as needing
review. Do not promise exactly-once shell execution. Retry transient dispatch
failure with bounded backoff; final failures enter an inspectable failed queue.
Use replay cursor plus periodic outbox sweep so disconnected streams lose no work.

Agent messages carry actor type, causation ID and conversation ID. An ACK,
self-addressed message, review notification, or broadcast does not generate an
automatic reply loop. Agent-to-agent task
requests may trigger another agent within configured permissions and a bounded
hop/turn budget (pilot default: 3 hops, 10 turns, 15 minutes per run). Reaching a
budget pauses with a visible reason; operators can resume. No global broadcast
fanout to all models by default. Pausing a project stops new dispatch; cancel is
cooperative and does not delete artifacts or mark tickets complete.

Acceptance target (not an observed result): idle managed agent starts within 5s
of a committed task message on a healthy local system. Failed preconditions must
show an explicit reason rather than a false green status.

## Remote continuous adapter

`remote` is an explicit harness label, never an alias for another installed
model. This matters for a seat such as `grok-worker`: registering it as Cursor
would launch Cursor under a Grok name. Configure the real boundary instead:

```sh
tickets join grok-worker --harness remote --wake-mode continuous
tickets master cos grok-worker
tickets hooks remote --agent grok-worker --prompt-kind cos
```

The schema-2 manifest provides the full fenced bridge sequence: `register`,
`heartbeat`, long-poll `next`, `start`, `end`, and `release`. `next` waits and
atomically claims one wake; `tickets pending` remains a read-only diagnostic.
Only one unexpired bridge lease exists per seat, and every mutation checks both
its bearer ID and monotonically increasing fence. A reconnect after expiry gets
a higher fence. If the old run had started, the new bridge sees
`recovery-required` and must explicitly retry instead of duplicating uncertain
external effects.

The bridge feeds the returned bounded prompt to its model session, runs board
commands through the pinned wrapper, and reports measured tokens/cost with
`end`. If no bridge is connected, the dashboard says **Wake queued — adapter
offline** and the unread message remains the queue. `tickets spawn` refuses a
bare `remote` harness instead of silently substituting Claude, Codex, or Cursor.

Local and remote adapters share the same delivery rules: one watcher/bridge
lease per seat, message-recipient dedupe, capped wake summaries (five messages,
320 characters each), run heartbeat, and stale-lock recovery. Harness cost is
recorded only when the harness reports it; an unreported run remains unmeasured.
Dispatch failures use bounded backoff and end in a visible durable `failed`
state while the wake stays queued. A manual remote retry or a new local trigger
can resume work without an unbounded paid loop.

These runtime commands are implemented in the root/live single-file CLI. The
smaller `pyproject.toml` console entry point does not yet expose watch, hooks,
UI, wake-mode, or remote protocol commands.

## Team interface

Messages has a channel rail, conversation pane, and optional thread/task pane.
Starter channels: #general, #work, #reviews; project members can create channels.
Members list identifies Human / Agent / Master, availability and project role.
DMs support human↔human, human↔agent and agent↔agent. Threads keep task follow-ups
attached; ticket chips open detail. Composer provides mentions, Send and Send task,
with a visible recipient and wake behavior before sending. Enter sends, Shift+Enter
adds a line; accessible labels, focus and unread counts are required.

Simple team access is now in scope: owner issues an expiring single-use invite;
member exchanges it for a server session. Owner/admin controls membership and
agent enrollment; members chat and request work within their project; viewers
read permitted channels. Private channels and DMs require explicit membership.
Agent sender identity comes from its credential, never from request body fields.
Authorize reads, sends, subscriptions, searches, wake dispatch and message-to-task
actions. Revocation cancels pending unauthorized deliveries; edits cannot silently
change instructions on a started run (send a versioned follow-up instead).
Messages inherit project retention; preserve minimal audit references after body
expiry. Show concrete hook delivery errors without including tokens/transcripts.

## Additional records and endpoints

Member, Invitation, Channel, ChannelMember, Message, Thread, Delivery, WakeJob,
Run and RunnerLease. Message has immutable ID, author, channel/DM, thread, body,
ticket_id, intent, mentions, version, causation_id and timestamp. Run has recipient,
session ID, ticket claim, state, timestamps and terminal reason.

POST /api/v1/invitations; POST /api/v1/invitations/exchange;
GET/POST /api/v1/channels; POST /api/v1/channels/{id}/members;
GET/POST /api/v1/messages; POST /api/v1/messages/{id}/task;
GET /api/v1/messages/{id}/deliveries; POST /api/v1/runners/register;
GET /api/v1/runners/jobs; POST /api/v1/runs/{id}/events;
POST /api/v1/runs/{id}/cancel. SSE carries authorized message and receipt events.
All mutations use request_id/version/lease where applicable from the base contract.

## Required evidence

Send a task DM to an idle managed Claude, see a claim and actual run start, then
receive its reply in the original thread. Post a task in #work, see master route
it once to a capable worker. Duplicate delivery/reconnect produces one active
run; busy agent has no parallel run. Offline and hook-only connections show the
correct reason. Test private channel access, spoofed sender, revoked membership,
dependency-blocked work, restart between claim and spawn, approval-required work,
agent reply loops and pause/cancel. No live board or real credentials in fixtures.
