# Dependent notes — what each lane needs from the T-178 freeze

Written for someone who has **not** read `docs/interface-v1.md` or
`docs/messages-and-runners.md`. Everything below is in `openapi.yaml`; this file
says which parts matter to you and which are traps.

Start by running the fixtures — they are the fastest way to see the shapes:

```sh
pip install -e '.[contracts]'
python -m pytest -q
ls tests/fixtures/          # 126 fixtures, grouped by screen
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
9. **No third, master-authority credential.** (T-224 ruling, planner 18:05Z,
   resolving T-211 §6.) Any registered agent may take the master lease; the
   operator session gates only the destructive actions (accept review, pause,
   assign, take master). A credential that authorized the master seat would,
   by construction, be a credential the *dead* master held — gating seizure on
   it breaks the exact recovery path `MASTER.md`'s HANDOVER procedure exists
   for ("masters are expected to die; any living agent may `tickets master
   take`"). The control against impersonation is the lease plus an audit
   trail (T-182), not authentication — visible-and-recorded beats forbidden
   here, because the board's real failure mode is masters dying silently, not
   masters being impersonated on a loopback-bound board. Revisit only
   together with decision 11 below, never separately.
10. **No dual-write bridge; V1 has no cutover to gate.** (T-224 ruling, planner
    18:05Z, resolving T-211 §3 legacy-writer handover; corrected on reopen —
    the first draft of this decision wired the flip to T-185/T-190 passing,
    which the planner struck as a V1 amendment smuggling in a V2 event.) A
    dual-write bridge means two writers against one board with no shared
    transaction — the same failure class as the T-215 cross-repo close and
    the T-202 double-build, introduced deliberately at the exact moment a
    migration bug would be hardest to tell from a product bug. There is no
    calendar freeze date *and no evidential one either*: the `.tickets` CLI
    stays authoritative for the file-based board operators coordinate on today;
    T-180's server is authoritative only for its own database. `409
    legacy_writer_active` is implemented in the contract now but stays OFF in
    V1 — it is the documented mechanism a *future* V2 cutover will flip, not
    something T-185/T-190 passing triggers on its own. Nothing in V1 requires
    the CLI to go read-only.
11. **No operator sign-in / session-mint route in V1.** (T-224 ruling, planner
    18:05Z, resolving T-211 §7.) See `openapi.yaml`'s `operatorSession`
    scheme description for the contract-prose statement dependents must not
    infer past. Confirmed out of scope for V1 (loopback-only); a mint route
    is the first non-loopback surface on the board and drags TLS termination,
    session lifetime, CSRF refresh and credential storage in behind it — all
    deferred by this pack's closing section. The limitation is written down;
    the route is not added.

12. **Broadcast is kept and modeled, not dropped.** (T-224 ruling, planner
    17:55Z, resolving T-211 §4 / amendment item 9.) `tickets msg` with no
    `--to` maps to a reserved board-ops channel every agent subscribes to.
    T-187's no-global-fanout rule survives intact: an unaddressed post there
    wakes the channel's `designated_master`, not every agent — which is
    already today's actual behavior (a broadcast lands in inboxes; it does
    not launch idle agents). Dropping broadcast without this replacement
    would silence coordination traffic the board is actively built on.

Changing any of these after freeze is a breaking change: it needs the master,
an `info.version` bump, and a check on which lanes already built against the old
shape. The process is in `README.md` under "Freeze rules". (Decisions 9-12 are
documentation of already-frozen or newly-ruled behavior — decisions 9, 11 and
12 change no schema field or route; decision 10 likewise names a sequencing
call, not a schema edit. None needs a version bump on their own account. See
`openapi.yaml`'s `operatorSession` description for the one prose addition made
alongside decision 11, and "Amendments after freeze" below for the six edits
that DO bump `info.version`.)

---

## Amendments after freeze (T-224)

T-211 mapped the CLI-vs-contract mismatch; the planner ruled per-item
(17:55Z, in T-224's notes) by one principle: *the contract stays frozen
except where leaving it as-is would make V1 impossible (a required V1
behavior cannot be expressed) or dishonest (the schema would force
publishing something known false).* Six edits met that bar — nothing else
touches `openapi.yaml` under this ticket. `info.version` bumped
`1.0.0-rc.1` -> `1.0.0-rc.2` for this batch. Per-item, against README's
freeze rules:

- **Breaking (rule 3), forcing the bump (rule 4):** relaxing
  `CreateTicketRequest.outcome`/`.acceptance` from required to optional
  changes what a previously-invalid request means (an omitted `acceptance`
  now validates, so a client relying on the 422 to catch its own bug no
  longer gets one), and requiring `GitEvidence.repository` rejects requests
  a dependent may already send without it. Both are deliberate: rule 3 exists
  to make a lane raise this with the master before merging, which is exactly
  what T-224 did (planner sign-off, 17:55Z/18:05Z).
- **Additive (rule 2), no version bump required on their own account:**
  `Ticket.priority/epic/sprint/needs`, the widened `AgentId` pattern (a
  regex superset — every string the old pattern accepted still matches) and
  the new `POST /tickets/{ticket_id}/reopen` route are all new-optional or
  new-route, the textbook additive case.
- **Judged additive, stated explicitly rather than assumed:** a new
  `AgentState` enum member (`limited`) and a new optional `Agent.limit`
  object are not covered by either README rule as written — expanding a
  closed enum can break a consumer that switches on it without a default
  case. Ruled additive here because no such consumer exists yet: T-180 (the
  only implementation of this contract) has not shipped, so there is no
  deployed client to break. This judgment call does not survive T-180
  shipping — a *future* enum addition, once a real dashboard renders
  `AgentState`, must be re-examined against actual switch-statement
  exhaustiveness, not assumed additive by analogy to this one.

All six land in one version bump rather than split, since they land in one
commit.

| # | Amendment | Rationale |
|---|---|---|
| 1 | `Ticket` gains optional `priority` (1-3), `epic`, `sprint`, `needs` (enum array). | T-184's dashboard must reproduce sprint progress and priority routing or it is a worse view of the board than `tickets board` already is. Optional, never required — a ticket with no epic is normal. Resolves T-211 amendment item 2 and T-229 finding (2) (`suggested` considered and explicitly rejected as a field — see "documentation only" below). |
| 2 | `CreateTicketRequest`: `title` stays required; `outcome`/`acceptance` become optional (documented `""`/`[]` defaults). `outcome`'s `minLength: 1` and `acceptance`'s `minItems: 1` dropped; the now-unreachable-from-create framing on the `missing_acceptance_criteria` error's description is removed (the code itself stays — it is still reachable from the review-submission gate in `storage/board.py`, a separate, still-live rule that a ticket needs acceptance before entering review). | Forced, not preferred: legacy import must be lossless and many existing tickets have no acceptance field — a required criterion 422s the import of a real board. The quality argument for requiring them is real and rejected anyway: a required free-text field gets satisfied with "n/a" within a day, and then the schema lies. Ticket quality is enforced by the master in routing, not `minLength`. Resolves T-211 amendment item 3. |
| 3 | `AgentId` pattern widened to `^(agt_[0-9a-z]{8,32}|[a-z][a-z0-9-]{1,31})$`. | Typical live agent names do not match `agt_*`; renaming every identity at cutover touches every brief, hook config and `tickets msg --to` on the board. The human-readable name IS the V1 identity; the `agt_` form stays legal for a server-minted id later. Resolves T-211 amendment item 4. |
| 4 | New route `POST /tickets/{ticket_id}/reopen` (`ReopenTicketRequest`: `request_id`, `expected_version`, `reason`; operator-gated). Returns the ticket to `open` with owner/owner_session cleared, distinct from `decideReview`'s reject-to-`claimed`. | `MASTER.md` HANDOVER step 2 (recover a silent claim), T-182's assignment loop and T-185's recovery verification all depend on an operation the contract could not express at all. This is the "impossible" bar: recovery is unimplementable without it. Resolves T-211 amendment item 6. |
| 5 | `GitEvidence.repository` promoted from optional to `required`. `sha`'s 40-hex shape is explicitly NOT relaxed. | T-229 finding (1): `repository` already existed as an optional field — the real fix was always "require it," not "add it." An unqualified sha with no repository is how an unrelated repo's merge can appear to "contain" evidence that was never built there (T-215's live failure). Relaxing `sha` instead would fix the validation error and leave the actual bug armed — legacy `branch@shortsha` evidence is archived verbatim and reported as a mismatch, never fabricated into a fake `GitEvidence` (T-213's settled call, upheld independently twice). Resolves T-211 amendment item 7 / §9, as corrected by T-229. **Consequence found while implementing, reported here since it goes beyond the one-line schema edit**: the legacy importer (`storage/legacy.py: _evidence()`) has no source of repository identity at all today, so it now withholds `GitEvidence` unconditionally (bumping a new `"no repository identity"` mismatch count) rather than fabricate one — including for the one sample ticket whose commit was already a real 40-hex sha, which previously did get real evidence. Zero reviews import from either sample legacy board until a caller can supply real repository identity (T-215's CLI-side work, not done here). Tests updated: `tests/storage/test_legacy_import.py`, `tests/storage/test_contract_conformance.py`. |
| 6 | `AgentState` gains `limited`. `Agent` gains optional `limit` (`AgentLimitDetail`: `source` enum `manual`/`derived`/`heuristic`, plus nullable `since`, `until`, `note`). | T-229 finding (4), promoted from a gap to required: a hard monthly limit and a plan-wide session limit both happened with no way to represent either. A dashboard that cannot say "out on a usage limit until X" renders a dead agent as idle — the exact confident-wrong-answer failure the board already suffers. T-230's liveness-truth spec (same session, CLI side) independently reaches a sharper conclusion than the original draft of this row assumed: `limited` is not just self-reported, it is self-reported *only*, with no way to tell a confirmed signal from a guess. A single free-text `limit_reason` cannot carry that distinction, so this was corrected during review from a flat `limit_reason`/`limit_until` pair to the structured `AgentLimitDetail` object, whose `source` field is exactly the manual/derived/heuristic taxonomy T-230 defines. `note` stays free text — the real causes seen this sprint (session cap, monthly cap, auth failure) do not share one closed taxonomy. |
| 7 | New route `POST /agents/{agent_id}/enrollments` (`ReEnrollAgentRequest`: `request_id`, `expected_version`, `role`, plus the same optional fields as create; response reuses `CreateEnrollmentResponse`). Operator-gated; only `revoked` agents. | T-275/T-405: a revoked agent could only return under a NEW name via `POST /enrollments`, stranding tickets and lease history on the old identity. Same-identity recovery is the adapter path `POST /sessions` was designed for once the operator has minted a fresh code. Lapsed agents are out of scope — `POST /hook-events` already heals them. |

Documentation only, no schema change (T-211 items 1, 5, 10, 13 and T-229
findings (2), (3)):

- **Item 1 (field names)**: the CONTRACT names win; nothing is renamed in
  `openapi.yaml`. T-179/T-213 build a real storage layer, not a
  `.tickets/*.json` passthrough, so `cli-mapping.md` §1's table is the
  normative legacy-importer translation, not a proposal.
- **Item 5 (claim idempotency / session lease)**: server-only, permanently.
  The CLI's O_EXCL lock is race-safe and replay-unsafe and never gets
  `request_id` or a session lease — a deliberate limit of the legacy path
  per decision 10 above, not a TODO.
- **Item 10 (`ready` vs `open`)**: Overview's dependency-aware `ready` count
  and `tickets board`'s dependency-naive `open` count are both correct and
  will differ for as long as the CLI and server run side by side. Not a bug
  in either.
- **Item 13 (`hook_schema_version`/`occurred_at`)**: the contract already had
  this right — `HookEvent.occurred_at` is required, `hook_schema_version`
  was never a real field. Strike the assumption from `docs/interface-v1.md`
  and `docs/messages-and-runners.md` wherever implied (T-181's doc edit, not
  done here); `occurred_at` is adapter receipt time, never agent-reported.
- **T-229 (2), `suggested`**: CLI-only routing output (`cmd_route` writes
  `t['suggested']`). Derived, not stored — no contract field.
- **T-229 (3), the `tickets route` subsystem**: explicitly out of scope for
  the V1 API (no endpoint reproduces `tickets next`'s ordering from the
  contract alone). Named here so the omission reads as a decision, not an
  oversight; a future `GET .../routing` read-only endpoint is a candidate
  for its own ticket if a lane needs it, not a T-224 drive-by.

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

---

## Amendments after freeze

- **T-210, fixture-only, no schema change**: `tests/fixtures/tickets/{request-review,detail-accepted,detail-review-pending,detail-review-rejected}.json`
  and `tests/fixtures/overview/populated.json` had `"repository": "advitiyavashist/tickets"`
  (the real project's identity, not a demo value) baked into the `repository`
  field's example data; replaced with `"demo-org/demo-repo"`. No field, type
  or schema changed, so nothing dependent on the shape of these fixtures is
  affected — only the literal string value of one example field. `docs/interface-v1.md:5`
  had the same real identity in prose and got the same substitution.

- **T-297, additive route, no `info.version` bump**: new
  `POST /tickets/{ticket_id}/acceptance` (`setTicketAcceptance`, request
  `SetTicketAcceptanceRequest`: `request_id`, `expected_version`,
  `acceptance` with `minItems: 1`), returning the `Ticket` at its new version.
  New error code `acceptance_not_editable` (409). Two new fixtures:
  `tickets/request-set-acceptance.json` and
  `errors/409-acceptance-not-editable.json`.

  **Why it exists — this is a gap in amendment 2 above, not a reversal of
  it.** Amendment 2 made `acceptance` optional at create and moved ticket
  quality from a create-time schema minimum to enforcement "later", at the
  review gate. That gate is real and still live: `store.transition` and
  `store.submit_review` both refuse `review` on an empty acceptance list.
  What amendment 2 did not notice is that "later" had no door — no route
  could write `acceptance` after create, so a ticket that arrived without
  criteria could never reach review through the API at all, by any caller,
  including the master. Measured on the live board while implementing this:
  all 217 tickets lack the field, and 29 of them are not `done`.
  `storage/legacy.py:_import_ticket` writes `acceptance` as the SQL literal
  `'[]'` and never consults the legacy item, so this is *every* imported
  ticket — T-213's import was lossless in storage and lossy in capability.

  **What this does NOT change, deliberately.** The create-time optionality of
  amendment 2 is untouched — imports stay lossless. The review gate is
  untouched — relaxing it to allow review with an empty list was the
  considered alternative and was rejected, because it deletes the quality
  guarantee rather than relocating it.

  **If you consume tickets:** a ticket's `acceptance` can now change after
  creation, so it is no longer safe to treat it as create-time-immutable.
  It is `expected_version`-guarded like every other mutation, so a stale read
  is a 409, not a silent overwrite.

  **If you write tickets:** the route replaces the list outright rather than
  appending — `expected_version` already gives you read-modify-write, and a
  separate append verb would only let two writers interleave into a list
  neither intended. It is refused with 409 `acceptance_not_editable` on a
  `done` ticket (terminal) and on one in `review` (an in-flight reviewer is
  judging against the list as it stands). A rejected review returns the
  ticket to `claimed`, which is writable — so the ticket stranded by a
  rejection, which is the case that reaches imported tickets that arrived
  already in `review`, is recoverable.
