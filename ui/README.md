# Ticket Board dashboard UI

The five screens in `docs/interface-v1.md` — Overview, Tickets (with a detail
panel), Agents, Messages (stub — full UI is T-189) and Activity — plus the
Connect-agent dialog and the Master panel, **reading and writing a live board
over HTTP and SSE**.

T-183 built these screens against fixtures on disk. T-184 replaced that: no
screen imports a fixture any more, every read is a request, every control is a
write, and the freshness line at the top of each screen is a claim this build
can defend. `src/fixtures/` survives only as contract-exact sample payloads for
the tests.

## Run it

You need two processes: the board, and this dashboard in front of it.

```sh
# 1. the board (writes operator-session.json 0600 next to the db)
python3 -c 'from ticket_board.server import serve; serve("board.sqlite3", port=4319)'

# 2. the dashboard, pointed at the board's state directory
npm install                                   # from the repo root (npm workspace)
BOARD_STATE_DIR=$PWD npm run dev -w ui        # http://localhost:5173
```

`npm run preview -w ui` serves the built bundle the same way.

### Why the dashboard needs a host, and what it does

The frozen contract has **no operator sign-in route** (`docs/api-notes.md`,
"Two contract gaps"), so a dashboard session is bootstrapped out of band:
`serve()` writes `{project_id, session_token, csrf_token, url}` to
`operator-session.json` at mode 0600. The planner's 12:50Z ruling described the
browser picking that up via a one-time `?token=` on a UI root.

**That UI root does not exist in this build, and could not.** The board serves
its API at the *root* path, hosts no static files, and sends no CORS headers of
any kind; `check_csrf` additionally refuses an unsafe cookie-authenticated
write whose `Origin` is not the board's own. So a dashboard served from any
other origin cannot read the board, cannot write to it, and has nowhere to hang
a `?token=` exchange. Building that root means editing
`src/ticket_board/server/`, which belongs to another lane.

`ui/board-host.ts` closes the gap from this side. It is a vite dev **and**
preview plugin that:

- answers `GET /__board/session` with the project id and CSRF token — **and
  never the session token**;
- forwards `/api/*` to the board with `/api` stripped, the `tb_session` cookie
  attached, and `Origin` rewritten to the board's own base URL;
- streams `text/event-stream` through unbuffered.

The browser is therefore same-origin with everything it talks to, and the
credential stays in the host process — **tighter than the `?token=` shape it
stands in for**, where the token would be readable from `document.cookie` and
would pass through the address bar. `src/session.ts` still supports the
`?token=` form as a fallback for whoever builds a real static root later; it
strips the token from the URL the moment it reads it.

This is a local operator convenience with the same threat model as the file it
reads: whoever can reach it is the operator, because whoever can read
`operator-session.json` already was.

### V1 only: this host exists in dev and preview, and in no production build

**Stated so the next person reads it as a boundary and not as a bug.** E-010 V1
is deliberately local-first, and the token path above is scoped to that.

`boardHost()` registers exactly two hooks — `configureServer` and
`configurePreviewServer` — so it runs under `vite dev` and `vite preview` and
**nowhere else**. `npm run build -w ui` emits static assets only; nothing in
that output answers `GET /__board/session`, proxies `/api/*`, attaches a
cookie, or rewrites `Origin`.

Be precise about what that leaves, because the bundle was checked rather than
assumed. `grep` over `dist/` finds **no** trace of the plugin
(`ticket-board-host`, `http.request`, `createServer`: 0 hits). What *does*
survive is the client half: the `HOSTED` probe still asks for
`/__board/session` and, in a production build, **nothing answers it**; and the
`URL_TOKEN` fallback survives intact, so `?token=` still works — but only where
the page is already same-origin with the board, since it sets `document.cookie`
and the board sends no CORS headers. Nothing serves the bundle from the board's
origin today, so in practice a statically-hosted dashboard resolves no session
and says so on screen.

That is the designed V1 state, not a regression: there is no production
credential story yet, and inventing one here would have meant either editing
another lane's `src/ticket_board/server/` or making `?token=` the primary path,
which leaks the token into history, access logs and `Referer`.

**What serves the token in V1:** the vite dev/preview host process, reading
`operator-session.json` (0600, written by `serve()`) off the local disk on
every request, and never handing the session token to JavaScript.

**What a production seam has to provide**, all six, because each is a
constraint proved against a real board rather than assumed:

1. **One origin.** The board sends no CORS headers at all, so the bundle and
   the API must be same-origin — a reverse proxy in front of both, or a board
   that learns to serve static files.
2. **Session discovery** — answer `GET /__board/session` with `project_id` and
   the CSRF token *only*, or render `window.__BOARD_SESSION__` into the page
   (`src/session.ts`, `INJECTED`). The session token must stay server-side.
3. **Credential attachment** — add the `tb_session` cookie in the proxy, not in
   the browser.
4. **`Origin` rewriting** to the board's own base URL, or `check_csrf` refuses
   every unsafe cookie-authenticated write.
5. **`Content-Length` forwarding.** The board reads exactly that many bytes; a
   proxy that drops it falls back to chunked encoding, every write answers "A
   JSON object body is required", and the unread body desynchronises the
   keep-alive connection so the *next* request on it fails too. This bit me in
   `board-host.ts` and it will bite the next implementation identically.
6. **Unbuffered `text/event-stream`**, or the live board never streams.

The real fix above all six is a **sign-in route**, which the frozen contract
does not have — that is a contract amendment and its own ticket (T-211's plan),
not something a deployment lane should improvise. Until then, reading a 0600
file off local disk is the whole credential story, and it does not leave the
machine.

## What the dashboard will not do

These are the ticket's "prevent optimistic false success" requirement, spelled
out as rules rather than as intentions. Each is tested.

- **Nothing on screen changes until the server says it did.** Every write goes
  through `state/useMutation.ts`, which builds its result from the returned
  record and never from the request. A refused write shows the refusal and no
  success line.
- **Freshness is the worse of two judgements.** `state/connection.ts` combines
  the server's `stream.state` with what this client observes about its own
  socket, and displays the worse. A snapshot that said "live" when it was
  written is not evidence about now, and when the client downgrades the server
  it says so on screen.
- **Never connected is not the same as an empty board.** With no session the
  app renders "Not connected" and no board data at all — a screen of zeroes
  would be a dangerous thing to show someone whose board is unreachable.
- **One `request_id` per intent, reused when retrying that intent.** A fresh id
  per retry defeats idempotency; a shared id across different intents is what
  makes every fixture-driven mutation after the first fail with
  `request_id_reused` (`docs/api-notes.md`).
- **A review's SHA is pinned, never typed.** `ReviewDecisionRequest.evidence_sha`
  must equal the SHA on the submitted review, so the operator is shown that SHA
  and the dashboard sends exactly it. Accepting with a failed check is refused
  before the board has to refuse it.
- **`superseded: true` is its own state**, rendered as kept-in-the-trail *and*
  changed-nothing — not as a success and not as a failure.
- **Agent-only actions are not offered to the operator.** Claim, update and
  request-review need a runtime session an operator session does not have, so
  the detail panel explains that instead of showing a button that could only
  ever 403.

## Test

```sh
npm test -w ui       # vitest — unit, screen, and live-server integration
npm run build -w ui  # tsc -b && vite build — also the type-check gate
```

`tests/ui/integration/` runs against a **real board server on a real socket**,
spawned per file by `board-server.ts`. That is where anything only visible with
a server on the other end is proved: real cookies and CSRF refusals, a real SSE
socket that can be cut and resumed, real idempotency keys being spent, real
409s from two clients racing one claim. The rest of `tests/ui/` mounts screens
against a mocked fetch, using payloads from `src/fixtures/data/` — which
`tests/ui/fixtures-parity.test.ts` keeps byte-identical to the canonical
`tests/fixtures/`, so screen tests assert against shapes the board really
produces.

## Copy rules enforced here

Centralized in `src/copy.ts` and `src/errorCopy.ts`, covered by
`tests/ui/copy.test.ts`:

- A reservation is **"Queued — waiting for agent"**, never "Working".
- A hook `stop` event is **"Turn finished"**, never "Ticket complete".
- A dependency-blocked ticket is **"Waiting on DEMO-13"**, never implying work
  started.
- `EmptyState.headline` / `AuditEvent.summary` are server-supplied copy —
  rendered verbatim, never re-derived locally.
- Every contract error code maps to what the operator should *do*; a code with
  no mapping falls through to the server's own message rather than getting
  invented copy.

## Why a workspace root package.json

`tests/ui/` sits next to `ui/` (matching this repo's `tests/<lane>/`
convention — see `tests/storage/`, `tests/server/`), not inside it. Plain Node
module resolution only walks *up* from an importing file, so
`tests/ui/*.test.tsx` cannot see `ui/node_modules` on its own. The root
`package.json` declares an npm workspace so `npm install` (run from the repo
root) hoists `ui`'s dependencies to a root `node_modules` that both
directories can resolve against. `ui/package.json` still owns the dependency
list.

## Known gaps

Read these before extending; each is a real limit, not a caveat.

- **No production build story, on purpose.** `board-host.ts` is a vite
  dev/preview plugin and is absent from `npm run build` output, so a
  statically-hosted bundle has nothing answering `/__board/session` and (absent
  a same-origin `?token=`) resolves no session. This is V1's local-first
  boundary, not a defect — see "V1 only: this host exists in dev and
  preview" above for the six things a production seam must provide.
- **No browser screenshot.** This environment has no Chromium/Playwright
  binaries. The suite mounts real React trees in jsdom and drives a real vite
  server against a real board over HTTP, but no test renders pixels.
- **Response validation is still shallow.** `api/validate.ts` checks required
  top-level keys and primitive types, not the recursive schema — a wrong-typed
  *nested* field still reaches a screen. Inherited from T-203 and unchanged.
- **Reconnect backoff is tested by unit, not by a cut cable.** `backoffDelayMs`
  has direct tests and the integration suite proves a terminal refusal stops
  retrying, but no test kills a live socket mid-stream and measures the retry
  schedule.
- **Pagination is surfaced, not implemented.** The tickets list says "More
  tickets exist beyond this page" when the board returns a `next_cursor`; there
  is no "load more". Search filters what is loaded and says so.
- **The Messages screen is still a stub** — T-189 owns it, and `/messages`
  answers a contract-shaped 404 naming T-187 until then.
- **`npm audit`** reports a dev-only `esbuild`/`vite` advisory about
  cross-origin requests to the vite dev server. Not fixed here: the remedy is a
  breaking vite major bump. Note it interacts with `board-host.ts` — that
  plugin makes the dev server a path to an authenticated board, so on a shared
  machine bind it to loopback (vite's default) and treat it like the board.
- **The host reads `operator-session.json` on every request** rather than
  caching it, because `serve()` mints a new session on each start and a cached
  copy would serve a dead token after a board restart.
