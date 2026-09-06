# Dependent notes — what each lane needs from the T-178 freeze

Written for someone who has **not** read `docs/interface-v1.md` or
`docs/messages-and-runners.md`. Everything below is in `openapi.yaml`; this file
says which parts matter to you and which are traps.

Start by running the fixtures — they are the fastest way to see the shapes:

```sh
pip install -e '.[contracts]'
python -m pytest -q
ls tests/fixtures/          # 124 fixtures, grouped by screen
cat tests/fixtures/manifest.json
```

---

## Fixture layout — how to find the shape you need

`tests/fixtures/manifest.json` is the **authoritative** map: every key is a
fixture path relative to `tests/fixtures/`, every value is the name of the
component schema in `openapi.yaml` that the fixture validates against.

```json
{
  "tickets/detail-claimed.json": "TicketDetailResponse",
  "messages/request-send.json":  "SendMessageRequest",
  "errors/409-ticket-version-conflict.json": "ErrorResponse"
}
```

So: **path → schema name via the manifest**, not via a filename rule. Read the
manifest, do not infer. `test_manifest_and_fixture_tree_agree` fails on any
unlisted file or any missing file, so the manifest and the tree cannot drift.

The top-level directory is the screen or surface the fixture belongs to:

| Directory | Fixtures | Surface |
|---|---|---|
| `overview/` | 4 | Board overview screen |
| `tickets/` | 18 | Ticket list, detail, and mutation bodies |
| `agents/` | 11 | Agent list, enrollment, session credentials |
| `master/` | 10 | Master lease and assignment loop |
| `messages/` | 25 | Channels, DMs, sends, tasks, deliveries |
| `runners/` | 12 | Wake jobs, runs, runner leases |
| `hooks/` | 6 | Claude hook adapter payloads |
| `events/` | 6 | SSE stream frames and reconnect/replay |
| `errors/` | 28 | One per declared error code |
| `activity/` | 2 | Activity feed |
| `project/` | 2 | Project settings |

Filename prefixes are a **readability convention, not a rule the suite
enforces** — where a file does not fit one, the manifest still names its schema:

- `request-*` (28) — a request body you must accept.
- `response-*` (19) — a response body you must produce.
- `list-*` (11) / `detail-*` (8) — collection and single-record reads.
- `<status>-<code>.json` in `errors/` (e.g. `409-ticket-version-conflict.json`)
  — the status code is part of the name, and
  `test_error_status_matches_the_code_family` checks the two agree.
- State suffixes name the case: `-empty`, `-populated`, `-offline`, `-blocked`,
  `-stale`, `-revoked`, `-paused`, `-imported`.

Adding a fixture means adding its manifest entry in the same commit. Adding a
screen means adding an `-empty` fixture for it too —
`test_empty_state_is_covered_for_every_listing_screen` is not advisory.

---

## Decisions dependents must match

These eight calls **supersede the design docs**. They were judgment calls made
at freeze time, and `docs/interface-v1.md` / `docs/messages-and-runners.md` are
either silent on them or say something different. Match them; do not
re-litigate them from the design docs, and do not "fix" a fixture that looks
wrong against a doc — raise it with the master instead.

1. **Actor comes from the credential; an `actor` field in a body is rejected
   400.** The two design docs conflict here — `interface-v1.md` says every
   mutation carries `actor`, `messages-and-runners.md` says sender identity
   comes from the credential "never from request body fields". The stricter
   rule won. Mutation bodies carry `request_id` and `expected_version` (plus a
   lease field where required) and nothing else identifying. This one changes
   **every mutation signature**, so it is the decision most likely to bite you.
   See `MutationEnvelope`. Confirmed frozen by the planner.
2. **`dependency_blocked` is a derived boolean, not a `TicketState` member.**
   A dependency-blocked ticket is an `open` ticket with the flag set. Blocked is
   an explicit state; dependency-blocked is computed. Do not add it to the enum.
3. **Review acceptance pins a SHA.** `ReviewDecisionRequest.evidence_sha` must
   equal the SHA on the submitted review, and acceptance refuses if any check
   failed. Re-resolving a branch name at accept time would make "done requires
   acceptance of the exact submitted artifact" unenforceable.
4. **Tasks are created by `POST /messages/{message_id}/task`, not by `intent: task` on a
   send.** Outcome/assignee/ticket validation lives in one place.
5. **`started` is not spawn.** A run reports `started` only once its runtime
   session exists and, for an execution request, its claim succeeded; anything
   else is 422. Delivery is not acknowledgement.
6. **Heartbeat and progress are separate fields** on both `Agent` and `Ticket`.
   A live-but-stalled session must stay visible instead of hiding behind
   liveness.
7. **Every record carries `version`; leases also carry `epoch`.** Mutations take
   `expected_version` and fail the write on mismatch — never last-write-wins.
   The 409 body carries `expected_version` and `actual_version` in `details`.
8. **Five routes exist that no design doc lists**, added so each screen is
   actually buildable: `GET /tickets/{ticket_id}`, `GET /agents`, `GET /activity`,
   `GET /master`, `GET /members` — plus the review decision, blocked toggle and
   session-lease revocation the acceptance criteria require.

Changing any of these after freeze is a breaking change: it needs the master,
an `info.version` bump, and a check on which lanes already built against the old
shape. The process is in `README.md` under "Freeze rules".

---

## T-179 — transactional board state and legacy import

**You own the records, not the routes.** Implement storage for: `Project`,
`Ticket`, `TicketUpdate`, `Review`, `Agent`, `SessionLease`, `Assignment`,
`MasterLease`, `HookEvent`, `AuditEvent`, plus the messaging records if you get
there before T-187 (`Member`, `Invitation`, `Channel`, `ChannelMember`,
`Message`, `Thread`, `Delivery`, `WakeJob`, `Run`, `RunnerLease`).

Non-obvious requirements:

- **Every record carries `version`; leases carry `epoch`.** Mutations take
  `expected_version` and must fail the write on mismatch, not last-write-wins.
  The 409 body must carry `expected_version` and `actual_version` in `details`
  (see `tests/fixtures/errors/409-ticket-version-conflict.json`) so the caller
  re-reads in one round trip.
- **`dependency_blocked` is derived, never stored as a state.** `TicketState` is
  `open|claimed|review|done|blocked`. Compute the flag from dependency states.
  A dependency cycle is 422 and must create nothing —
  `tests/fixtures/errors/422-dependency-cycle.json`.
- **`request_id` is an idempotency key.** Same id + identical body returns the
  original result; same id + different body is 409 `request_id_reused`.
- **Late updates from a superseded session are kept, not dropped.** Store them
  with `superseded: true` and do not let them modify ticket state. See
  `tests/fixtures/tickets/detail-superseded-update.json`.
- **`last_progress_at` is not `last_heartbeat_at`.** Two fields, two meanings, on
  both `Agent` and `Ticket`. A live heartbeat with stale progress is the signal
  the Overview attention queue renders; collapsing them destroys it.
- **Legacy import:** retain the original files, document rollback, and reject
  direct legacy writes while server ownership is active with 409
  `legacy_writer_active`. `Project.source` is `native|imported_legacy`.

## T-180 — board API and scoped access

**Implement the routes in `openapi.yaml` as published.** Fixtures under
`tests/fixtures/overview/`, `tickets/`, `agents/`, `master/`, `activity/` and
`events/` are your expected response bodies.

Non-obvious requirements:

- **Actor comes from the credential, never the body.** A body containing an
  `actor` field is 400 `malformed_request`. This resolves a genuine conflict
  between the two design docs — see the README, and do not re-litigate it
  without telling the other lanes.
- **Two credential types, different powers.** `operatorSession` is an HttpOnly
  SameSite cookie with CSRF and origin checks. `agentToken` is a bearer token
  that must be **unable** to create enrollments, accept reviews, or take master
  authority — 403 `agent_token_insufficient`.
- **Project mismatch is 403 `forbidden_scope`, not 404.** Deliberate: 404 would
  make project existence probeable.
- **Claim is the concurrency-critical route.** Racing N claims yields exactly one
  owner; losers get 409 `ticket_already_claimed`. Capacity is 1 by default —
  409 `capacity_exhausted`. An expired session lease is 409
  `session_lease_expired`: **an agent whose adapter is failing cannot claim
  offline**, which is a release-acceptance item.
- **Review acceptance compares `evidence_sha` against the pinned review SHA**
  and refuses if any check is `failed` — both 422 `invalid_review_evidence`. Do
  not re-resolve the branch name at accept time.
- **The reviewer must differ from the submitter**, and the master cannot accept
  its own implementation — 403.
- **Every screen response embeds `stream` (`StreamStatus`)** so the UI can render
  "Connection lost. Showing updates from 14:32." without inventing a timestamp.
  Listing screens also carry a server-supplied `empty_state`.
- **SSE:** `id:` is the `event_id`; resume from `Last-Event-ID`. If that id has
  aged out, emit one `snapshot_required` with
  `resume_hint.reason = cursor_expired` rather than silently skipping —
  `tests/fixtures/events/snapshot-required-cursor-expired.json`. Emit
  `heartbeat` so a dead connection is distinguishable from an idle one. Filter
  every event by the subscriber's ACL before sending.

## T-181 — Claude hook adapter and doctor

Your surface is `POST /enrollments`, `POST /sessions`, `POST /hook-events`, and
`DELETE /agents/{agent_id}/session-lease`.

Non-obvious requirements:

- **`HookHealth` has four independent booleans**: `config_installed`,
  `server_received`, `response_delivered`, `session_adopted`. The doctor must
  show them separately. **A synthetic `probe` event must never set
  `session_adopted`** — a probe does not prove the real Claude session adopted
  the hook. See `tests/fixtures/agents/list-probe-not-adopted.json`.
- **Hook events carry bounded metadata only** — no prompts, tool arguments,
  transcripts, environment values or credentials. `note` is the single free-text
  field and exists only for explicit user notes.
- **Deduplicate on `event_id`.** A retried event yields one audit record and
  still returns a response with `deduplicated: true`.
- **Adapter failure is non-blocking for ordinary model work but never grants a
  claim.** Queue and retry with bounded backoff; 429 is expected under load.
- **The enrollment code is returned exactly once**, expires in 10 minutes, and
  goes to the agent over **stdin, not shell history**. It must never reach a URL,
  a log or a fixture. Invalid and expired codes return deliberately
  indistinguishable messages so the code cannot be probed.
- **Merge only your own hook entries** and preserve unrelated ones —
  `CreateEnrollmentResponse.config_changes` publishes exactly what will change.
- **Lease revocation requires an explicit note.** The server never revokes on
  silence; never declare death from silence.

## T-183 — dashboard screens against fixtures

Build against `tests/fixtures/` alone; do not wait for T-180.

- One fixture per screen **and** per empty state:
  `overview/`, `tickets/`, `agents/`, `activity/`, `master/`, `messages/`.
- Also stubbed for you: `overview/stale-stream.json` (stale banner),
  `overview/blocked-and-offline.json` (full attention queue),
  `agents/list-hook-only.json`, `agents/list-offline.json`,
  `tickets/detail-queued-assignment.json`.
- **Copy rules that are contract, not styling:** a reservation renders
  "Queued — waiting for agent", never "Working". An agent Stop is "Turn
  finished", never "Ticket complete". An unmet dependency renders "Waiting on
  DEMO-13" and never implies work started. `EmptyState.headline` is
  server-supplied — render it rather than hardcoding your own wording.
- All durations derive from the server timestamps in the payload.
- Every listing response carries `stream.state`; render the stale banner from it.

## T-187 — channels, membership and message delivery

Your surface is `/members`, `/invitations`, `/invitations/exchange`,
`/channels`, `/channels/{channel_id}/members`, `/messages`, `/messages/{message_id}/task`,
`/messages/{message_id}/deliveries`.

Non-obvious requirements:

- **Message and its outbox rows are written in one transaction.**
  `SendMessageResponse` returns both together; a message persisted without its
  deliveries is the bug this shape exists to prevent.
- **Author comes from the credential** — a body that sets one is 403
  `sender_identity_rejected`.
- **A private channel you cannot read is absent from `GET /channels`**, not
  returned with a flag. Reading it directly is 403 `not_channel_member`; a
  revoked member is 403 `membership_revoked`.
- **The receipt chain is a closed enum**: `sent → queued → delivered → started →
  responded`, with `blocked`, `awaiting_approval`, `canceled`, `failed` as
  visible alternatives, each carrying a `DeliveryReason`. Fixtures exist for
  runner-offline, manual-resume-required (hook-only), agent-busy,
  dependency-unmet, awaiting-approval, failed and canceled — see
  `tests/fixtures/messages/deliveries-*.json`.
- **Waking rules:** a DM or a mention wakes the named agent; an unaddressed task
  in a channel wakes that channel's `designated_master` to decide the owner;
  ordinary conversation reaches subscribers **without** launching every agent.
  There is no global broadcast fanout.
- **Receipts and replies do not wake their author** and do not generate another
  auto-reply. `causation_id` is how you break loops.
- **Bodies are immutable.** A correction is a new message with
  `supersedes_message_id`; edits must not silently change instructions on a
  started run.
- **`WakeJob` is at-least-once**, deduplicated on
  `(message_id, recipient_agent_id)` via `dedupe_key`. Runners must reconcile an
  existing local process after a crash before retrying. Do not promise
  exactly-once execution.

## T-188 / T-192 — runner and permission presets

`/runners/register`, `/runners/jobs`, `/runs/{run_id}/events`, `/runs/{run_id}/cancel`.

- **`RunnerLease` fences on `epoch`**; a second supervisor for the same agent
  gets 409 `run_already_active` rather than starting a parallel session.
- **`started` requires a live runtime session and, for an execution request, a
  successful claim.** Reporting it earlier is 422 `invalid_state_transition`.
  Process spawn is not proof of execution.
- **Budgets are enforced and visible**: 3 hops / 10 turns / 15 minutes by
  default. Reaching one pauses the run with a stated reason
  (`runners/response-run-budget-paused.json`), it does not fail silently.
- **Permission requests surface "Needs approval"** and pause —
  `runners/response-run-paused-approval.json`. Never show a false green.
- **Cancel is cooperative**: it retains artifacts and never marks a ticket
  complete.
- **Message bodies go to the runtime via stdin or structured input**, never
  interpolated into a shell command.

---

## Things this pack deliberately does not settle

- Storage engine details, migration mechanics and index choices — T-179's call.
- Pagination cursor encoding — opaque by contract; pick your own scheme.
- The SSE retention window length — pick one, then document it; the contract only
  requires that exceeding it produces `snapshot_required`.
- Rate-limit thresholds — 429 is specified, the numbers are not.
- Anything about hosting, TLS termination or remote exposure. V1 binds loopback.
