# Board API client (T-203) — handoff for T-184

Typed client for `docs/contracts/openapi.yaml` at tickets `origin/main@884be46`
(post T-178 freeze, post T-179/T-183 merge). **Fixture-replay tested only —
nothing here has ever called a live server.** `tests/ui/api/` mocks `fetch`
and replays canonical fixtures from `tests/fixtures/`; wiring this client to
the real T-180 API, and everything that only shows up once a server is on the
other end (timeouts, real cookie behavior, real reconnect timing), is T-184's
job.

## What is covered

- `BoardClient` (`client.ts`): one method per route the dashboard needs —
  `getOverview`, `listTickets`/`getTicket`, `createTicket`/`claimTicket`/
  `createTicketUpdate`/`requestReview`/`decideReview`/`setTicketBlocked`,
  `listAgents`/`createEnrollment`/`exchangeEnrollment`/`revokeSessionLease`,
  `getMasterPanel`/`takeMasterLease`/`setMasterPaused`/`createAssignment`,
  `listActivity`. There is no separate "done" route — accepting a review
  (`decideReview(..., { decision: "accept" }`) is what moves a ticket to
  `done`.
- `subscribeToEvents` (`sse.ts`): fetch-based SSE reader (not `EventSource` —
  it cannot send `X-Project-Id`/`Last-Event-ID`). Sends `X-Project-Id` on
  every connection (the stream is the one call where project scoping decides
  whose events you receive), resumes with `Last-Event-ID` on every reconnect
  after the first frame, routes `snapshot_required` to its own handler
  instead of treating it as a normal event, and exposes `heartbeat`
  separately so a caller can tell "dead" from "idle." SSE terminates each
  line with CRLF, CR or LF, chosen independently per line, so a frame
  boundary (a blank line) can be any of nine terminator pairs — matching a
  fixed alternation like `\r\n\r\n|\n\n|\r\r` still misses some (e.g. a
  `\n`-terminated data line followed by a `\r\n`-terminated blank line).
  `readFrames` instead normalises every line ending to `\n` before framing
  (holding back a trailing lone `\r` in case a chunk ends mid-CRLF, and
  flushing it when the stream ends there), so `FRAME_BOUNDARY` only ever
  needs to match the normalised `\n\n` form. Without this, a stream using an
  uncovered terminator pair stalls forever with no error. On a non-2xx response, a 4xx other than
  408/429 is treated as terminal (stops reconnecting, since a dead or
  unauthorized session returns the same status forever); 5xx, 408, and 429
  keep retrying.
- `BoardError` (`errors.ts`): every failure — the server's `ErrorResponse`
  envelope and this client's own boundary failures (`unexpected_status`,
  `invalid_response_shape`, `network_error`, `client_malformed_request`) —
  comes back as this one type. `.versionConflict()` returns
  `{expected_version, actual_version}` for a 409
  `ticket_version_conflict` so a screen can re-read in the same round trip
  (`docs/storage-notes.md` point 3); it returns `null` for anything else.
- Every mutation method rejects a body containing `actor` and rejects a
  non-numeric value in a field the contract types as a number
  (`expected_version`, `expected_epoch`, `lease_epoch`) **before** it reaches
  `fetch` — see `validate.ts` `assertMutationRequest`. The TypeScript types
  already make both impossible for a normal caller; this is the boundary
  check for a caller that reaches in with `as unknown as ...`.
- Every response is checked against a required-top-level-key shape (`validate.ts`)
  before it is returned, so a response missing a required field or with a
  wrong-typed key throws `BoardError("invalid_response_shape")` instead of
  reaching a screen half-formed.

## Known gaps — read before extending

- **Response validation is shallow, by design.** It checks required
  top-level keys and primitive types, not the full recursive JSON Schema in
  `openapi.yaml`. A wrong-typed *nested* field (say, a `checked` inside
  `Ticket.acceptance[]` sent as a string) will not be caught. Deep validation
  would mean pulling in an OpenAPI-to-JSON-Schema validator (ajv) plus a YAML
  loader for a package that only replays fixtures — not done here. If T-184
  needs it, generate the validator from `openapi.yaml` directly rather than
  hand-widening `validate.ts` schema by schema.
- **No real reconnect timing is exercised.** Tests use `reconnectDelayMs: 0`
  and a mock `fetch` returning a fixed sequence of responses. Backoff,
  jitter, and what happens when the network is actually flaky are T-184's
  (or T-185's) to prove against a live or simulated server. Deciding
  *whether* to retry at all (terminal 4xx vs. transient 5xx/408/429) is this
  client's job and is tested; the timing of retries is not.
- **CSRF**: `BoardClientConfig.csrfToken` is threaded through to
  `X-CSRF-Token` on unsafe operator-session methods only (never with an
  agent token, never on GET — tested in `tests/ui/api/client.test.ts`), but
  nothing here obtains or refreshes that token — that is session-management,
  which is T-184's screen-level concern, not this client's.
- **`/members` is intentionally not implemented.** It is a real route (see
  `docs/contracts/dependent-notes.md` decision #8) but reads as
  messaging-surface (T-187/T-189), and the ticket's stated scope for this
  client (overview, tickets, agents/enrollment, master, activity) does not
  list it. Add it here if a dashboard screen turns out to need it before
  T-189 ships.

## Fixtures

`tests/ui/api/fixtures/data/` is a **trimmed** copy of the routes this
client's tests exercise (not the full `tests/fixtures/` set, and not the
same trimmed set `ui/src/fixtures/` keeps for T-183's screens — that one is
scoped to screen scenarios, this one to route request/response pairs).
`tests/ui/api/fixtures-parity.test.ts` fails if any file in the copy drifts
from the canonical `tests/fixtures/` (compared byte-for-byte, not by parsed
JSON equality), and fails outright — not skip — if the canonical
`manifest.json` is missing. Same mechanism as `tests/ui/fixtures-parity.test.ts`
from T-183.
