# Messages that move work

V1 scope amendment, requested by user 2026-09-06. This replaces the earlier
deferral of automatic launching. Design and assignments only so far; no runtime
wake service has been installed. Delivery remains in standalone tickets repo.

## What happens when you send

- DM to an agent: creates a durable delivery and wakes its managed runner. Task
  instructions are linked to an existing ticket or create a draft for master
  routing. The agent claims eligible assigned work before implementation.
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

Agent messages carry actor type, causation ID and conversation ID. A reply/receipt
does not wake its author or generate another auto-reply. Agent-to-agent task
requests may trigger another agent within configured permissions and a bounded
hop/turn budget (pilot default: 3 hops, 10 turns, 15 minutes per run). Reaching a
budget pauses with a visible reason; operators can resume. No global broadcast
fanout to all models by default. Pausing a project stops new dispatch; cancel is
cooperative and does not delete artifacts or mark tickets complete.

Acceptance target (not an observed result): idle managed agent starts within 5s
of a committed task message on a healthy local system. Failed preconditions must
show an explicit reason rather than a false green status.

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
