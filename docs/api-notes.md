# API handoff (T-180) — what T-182, T-184 and T-187 need to know

Written for someone who has not read `src/ticket_board/server/`. It covers the
routes this build serves, the decisions that are not in `openapi.yaml`, and the
places where the frozen contract and the storage layer did not line up.

Read `docs/storage-notes.md` first if you are going to touch records; this file
assumes it.

```python
from ticket_board.server import BoardServer, serve

serve("board.sqlite3")                 # loopback, prints nothing, writes a session file
```

```python
board = BoardServer("board.sqlite3")   # in process, no socket
response = board.handle(request)       # wire.Request -> wire.Response
```

`board.handle` is the whole API. `httpd.py` is a thin
`ThreadingHTTPServer` adapter and the only module that knows HTTP has a wire
format, which is why almost every test in `tests/server/` runs without a socket.

Run `python -m pytest tests/server -q` to see every behaviour below asserted.

## What is here, and what is not

Implemented: `/overview`, `/tickets` (list, create, detail), `/tickets/{id}/claim`,
`/updates`, `/reviews`, `/reviews/{id}/decision`, `/blocked`, `/agents`,
`/agents/{id}/session-lease`, `/enrollments`, `/sessions`, `/hook-events`,
`/master`, `/master/lease`, `/master/pause`, `/assignments`, `/activity`,
`/events`.

**Not** implemented, and answering a contract-shaped 404 that names its owner in
`error.details.owner_ticket`: `/members`, `/invitations`, `/channels`,
`/messages` (**T-187**) and `/runners`, `/runs` (**T-188 / T-192**). A 404 that
says "T-187 owns this" is more useful to the console than a connection error and
cannot be mistaken for a working route.

The master *routing loop* is **T-182**. What is here is only what the frozen
routes need: a compare-and-swap takeover, a pause toggle fenced by the epoch, and
a reservation that refuses a superseded master. There is no sweep and no
eligibility policy.

## The seven things most likely to bite you

1. **`security` is a list of alternatives, so a request may satisfy a route with
   any credential it carries.** (`auth.in_scope` is the scope predicate;
   `_authorize` does the filtering, because it has to weigh every credential on
   the request rather than one.) A caller presenting both an operator cookie and
   an agent token is authorized as the operator on an operator-only route and as
   the agent on an agent route. Resolving one credential up front and *then*
   asking whether it fits the route is the obvious implementation and it is
   wrong: it refuses callers who did present something acceptable. See
   `_authorize`.

2. **`request_id` replay is applied to every mutation except four, and each
   exception has a reason.** Ticket create/claim/update/review/decision/blocked
   and all three master routes store and replay the original result.

   - `POST /enrollments` and `POST /sessions` are excluded because their
     response bodies are **one-time secrets**, and replay works by storing the
     response — which is exactly what "returned exactly once" forbids. A
     replayed `POST /sessions` fails as `enrollment_code_invalid`, the correct
     outcome: the code was already spent.
   - `POST /hook-events` deduplicates on `event.event_id` instead. That is the
     contract's rule for this route, and it is the right key: the adapter
     retries the same *event*, not the same request.
   - `DELETE /agents/{id}/session-lease` is not replayable; a second call is 409
     `session_lease_expired` with `reason: already revoked`. Revocation is a
     deliberate act with a note, and answering "already done" is more useful to
     an operator than silently repeating the first one. Its `request_id` is
     validated but does not reach the store's audit row, because
     `BoardStore.revoke_session` takes no `request_id` — a small gap, named
     here rather than papered over.

3. **Cursors are opaque but typed.** `views.encode_cursor(kind, value)` wraps the
   position with the listing it belongs to, so an activity cursor replayed
   against `/tickets` is a 400 rather than a wrong page. Ticket pages are keyed
   on the ticket id, activity on the audit `seq`. Both are ours to change; do not
   parse them.

4. **`Agent.state` is derived at read time, not stored.** The column is written
   once at enrollment and nothing keeps it current, so a cached `working` can
   outlive its session. The priority order is: `revoked` (lease revoked) >
   `offline` (a hook `last_error`, or no heartbeat for 5m) > `working` (owns an
   active ticket) > `idle` (a live session lease) > `connected` (hook activity
   but no live lease) > `idle`. Note that the published fixtures' `state` values
   are illustrative and not all mutually consistent — `agents/response-enrollment.json`
   shows `connected` with every `hook_health` boolean false. Build against this
   table, not against a fixture's `state`.

5. **Lease *expiry* and lease *revocation* are different things and only one of
   them is a judgement.** Expiry is the clock: it stops that session claiming new
   work and says nothing about the agent. Revocation is an explicit act with a
   note, and it takes the agent's bearer token with it. The server never revokes
   on silence.

6. **The SSE retention window is 1000 events** (`views.SSE_RETENTION_EVENTS`).
   The contract requires a documented window, not a length. Resuming from an id
   older than that yields one `snapshot_required` with
   `resume_hint.reason = cursor_expired`.

7. **Heartbeat and `snapshot_required` frames carry no SSE `id:` line.** Their
   JSON still has an `event_id` because the schema requires one, but a client
   must not adopt it as a cursor: it is not a position the trail can replay from.

## Credentials

Two types, different powers, both stored as SHA-256 hashes. The raw value exists
in the response that mints it and nowhere else — not in a row, not in a log, not
in an error message.

| | `operatorSession` | `agentToken` |
|---|---|---|
| carried as | `tb_session` cookie (HttpOnly) | `Authorization: Bearer` |
| unsafe methods need | `X-CSRF-Token` + an allowed `Origin`/`Referer` | nothing extra |
| may | enroll agents, decide reviews, take master authority | claim, update, submit reviews, post hook events |
| may not | claim through a runtime session (it has none) | the four operator powers — 403 `agent_token_insufficient` |

CSRF applies to cookie-authenticated writes only, because a bearer token is not
attached by a browser to a cross-site request. An unsafe cookie request with **no**
`Origin` and no `Referer` is refused rather than given the benefit of the doubt.

**Cross-project access is 403 `forbidden_scope`, never 404** — frozen, so project
existence is not probeable. A project that does not exist answers the same way.

## Two contract gaps, raised rather than fixed

Both were posted to the planner on 2026-09-06 and are implemented in the strict
reading of the freeze, behind a one-line seam.

1. **There is no operator sign-in route.** `openapi.yaml` defines
   `operatorSession` and publishes no route that mints one (`POST /sessions` is
   the *agent* enrollment exchange). So a dashboard session is bootstrapped out
   of band: `serve()` mints one and writes `{project_id, session_token,
   csrf_token, url}` to `operator-session.json` at mode 0600 next to the board
   file, the way a local notebook server hands out its token. On a loopback V1
   that is honest — whoever can read the server's state directory is already the
   operator — but it is a gap, not a design. **T-184 needs to read that file or
   be handed the pair.**

2. **Agent tokens cannot take master authority, but a fixture shows an agent
   holding the lease.** The `securitySchemes` text is explicit that an agent
   token must be refused for master authority; `master/panel-active.json` shows
   `holder.id = agt_master001`. Both cannot hold. The normative security scheme
   won: `/master/lease`, `/master/pause`, `/assignments` and the review decision
   are operator-session-only, and the holder is the operator actor typed as
   `{"type": "master"}`. **Consequence for T-182: the routing loop needs an
   operator session, not an agent token.**

## Four seams at the T-179 boundary

None of these are defects in the storage layer; they are places where the record
layer stops and the route layer has to finish the job. Listed because each is
invisible until it is wrong.

1. **`BoardStore._session_is_current` checks revocation, not expiry.** That is the
   right question for "is this update superseded" and the wrong one for a claim:
   the release bar is that an agent whose adapter has stopped delivering cannot
   claim offline, and that agent's lease is *expired*, not revoked. The API
   enforces expiry itself in `_require_live_lease`.
2. **`BoardStore.assign()` returns the raw row, including `project_id`.**
   `Assignment` is `additionalProperties: false` and has no such field, so the
   row cannot be returned as-is; `views.serialize_assignment` reshapes it.
3. **`submit_review` and `decide_review` do not take an `expected_version`**, and
   `decide_review` does not know who submitted. The version check, the
   ownership check and reviewer-is-not-submitter are enforced in the routes.
4. **`get_agent` does not join the session lease** that `Agent.session` publishes.
   `views.serialize_agent` joins it and adds `hook_health.last_error`.

Two smaller ones: the master lease has no store-side operations (this lane adds
them in `server/master.py`, using `BoardStore._replay`/`_remember` so a
`request_id` replay behaves identically on both sides — **T-182 may want those
promoted into `BoardStore`**), and a version conflict on a record that is not a
ticket reuses `ticket_version_conflict`, because it is the only version-conflict
code the frozen enum carries. The `details` name the real subject (`agent_id`),
so nothing is misreported; widening the enum would be a contract amendment.

## Policy numbers, all in one place

None of these are contract. They are named constants a deployment can move.

| Setting | Value | Where |
|---|---|---|
| operator session lifetime | 12h | `auth.OPERATOR_SESSION_SECONDS` |
| enrollment code lifetime | 10m (contract) | `auth.ENROLLMENT_CODE_SECONDS` |
| session lease lifetime | 30m, refreshed by each hook event | `auth.SESSION_LEASE_SECONDS` |
| heartbeat stale / offline | 90s / 5m | `views.HEARTBEAT_*` |
| progress stalled | 15m | `views.PROGRESS_STALLED_SECONDS` |
| SSE retention | 1000 events | `views.SSE_RETENTION_EVENTS` |
| SSE heartbeat | 15s | `events.HEARTBEAT_SECONDS` |
| hook rate limit | 120 events / 60s per agent, 429 with `retry_after_seconds: 5` | `hooks.RATE_LIMIT_*` |
| page size | 50 default, 200 max (contract) | `views.*_PAGE_LIMIT` |
| master lease TTL | 120s default, 15–600 (contract) | `master.DEFAULT_LEASE_TTL_SECONDS` |
| reservation TTL | 600s default, 30–3600 (contract) | `master.DEFAULT_ASSIGNMENT_TTL_SECONDS` |

Ticket ids are minted by the server — `CreateTicketRequest` has no id field. The
prefix is the project name's alphanumerics uppercased (`Demo` → `DEMO`), the
number is the highest existing plus one.

## Honest limits

- **Nothing here has been measured under load, and no latency number appears
  anywhere in this lane.** The concurrency evidence is an eight-way HTTP claim
  race on one machine that yields exactly one owner; that is a correctness claim,
  not a throughput one.
- **Loopback only.** `serve()` refuses a non-loopback bind unless the caller
  passes `allow_remote=True`, because this build has no remote operator
  authentication to bind to. There is no TLS termination here.
- **The SSE ACL filter is project scope and nothing else.** Every record this
  stream can carry is project-scoped today. The per-channel rule — a subscriber
  never receives events for a channel it cannot read — has exactly one place to
  live, `EventStream.visible_to`, and **T-187 has to fill it in**. It is a seam,
  not a completed check.
- **Listing filters run in process**, over the full project's tickets. Correct at
  V1 sizes; an index-backed query is a later concern, not a hidden one.
- **The hook rate limiter is per process and in memory.** It protects this
  server's write path; it is not an account quota and it does not survive a
  restart.
- **The enrollment 422 pair still differs by `code`.** The messages are identical
  and neither carries `details`, as the contract asks, but a caller who submits a
  real-but-expired code can still tell it was real, because
  `enrollment_code_expired` and `enrollment_code_invalid` are two frozen codes.
  Collapsing them is a contract amendment, not a route-layer decision.
- **`empty_state` on `/activity` is only reachable through a filter.** Creating a
  project writes an audit row, so an unfiltered activity feed is never empty.
  Do not wait for a state that will not arrive.
- **An unexpected server failure answers 500 with a body the contract cannot
  describe.** `ErrorResponse.status` is a closed enum of
  400/401/403/404/409/422/429 -- there is no member for "this server has a bug".
  Letting the exception escape instead would drop the connection, which a
  dashboard cannot distinguish from the board being down, so `handle` returns
  `{"error": {"code": "internal_error", "status": 500, ...}}` with no exception
  text in the message (a traceback string can carry a path, a query or a
  credential). It is a deliberate, named departure from the frozen schema, not
  an oversight; adding a member to the enum is a contract amendment.
- **Acknowledging an attention item is not possible.** `AttentionItem.acknowledged_by`
  is always `null`: there is no route in the frozen contract to set it, so the
  field is carried rather than invented.

## Evidence worth reproducing

`tests/server/test_api_conformance.py` validates **live responses** against
`docs/contracts/openapi.yaml` — not the fixtures, which `tests/test_contracts.py`
already covers. It carries two planted-defect tests (an extra field, and a `null`
where the contract wants the key omitted) so a green bar there is evidence rather
than decoration.

`tests/server/test_t200_harness.py` points **T-200's conformance harness** — another
lane's cases, credentials and pass criteria — at a real server. Every read screen
passes it and every mutation route refuses an `actor` in the body. The mutation
success cases mostly answer 409, and the cause is not in the routes: **every
request fixture in `tests/fixtures/` carries the same `request_id`**, so against a
real board the first mutation spends the key and the rest are
`request_id_reused`. Invisible against the fixture-replay stub. **T-185 will hit
this the moment it points `BOARD_URL` at a real server**; the same case passes when
run alone, which that test also proves.
