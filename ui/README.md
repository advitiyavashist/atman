# Ticket Board dashboard UI — T-183

Fixture-only build of the five screens in `docs/interface-v1.md`: Overview,
Tickets (with a detail panel), Agents, Messages (stub — full UI is T-189), and
Activity, plus the Connect-agent dialog and the Master panel. **No network
call happens anywhere in this package.** Every screen reads a named scenario
from `src/fixtures/`, which is a verbatim copy of the frozen contract's
`tests/fixtures/` (`opus-backend/e010-contract@855e15a` — see
`docs/contracts/manifest.json` there for what each file validates against).
Wiring these screens to the live API is T-184.

## Run it

```sh
npm install         # from the repo root — this is an npm workspace, see ../package.json
npm run dev -w ui    # http://localhost:5173
```

Every screen has a "Fixture scenario" picker in its header so you can switch
between the named fixtures (empty, offline, blocked, stale-stream, revoked,
probe-not-adopted, …) without needing a server. The header's
"FIXTURE MODE · NO LIVE DATA" badge and the per-screen "not live board state"
notes are load-bearing, not decoration — nothing in this build should be
mistaken for a live connection.

## Test

```sh
npm test -w ui       # vitest, jsdom + Testing Library, 30 tests
npm run build -w ui  # tsc -b && vite build — also the type-check gate
```

`tests/ui/fixtures-parity.test.ts` fails if `src/fixtures/data/` ever drifts
from the canonical `tests/fixtures/` this branch was cut from.

## Why a workspace root package.json

`tests/ui/` sits next to `ui/` (matching this repo's `tests/<lane>/`
convention — see `tests/storage/`, `tests/server/` in the other E-010
tickets), not inside it. Plain Node module resolution only walks *up* from an
importing file, so `tests/ui/*.test.tsx` cannot see `ui/node_modules` on its
own. The root `package.json` declares an npm workspace so `npm install` (run
from the repo root) hoists `ui`'s dependencies to a root `node_modules` that
both directories can resolve against. `ui/package.json` still owns the actual
dependency list.

## Copy rules enforced here

Centralized in `src/copy.ts`, covered by `tests/ui/copy.test.ts`:

- A reservation is **"Queued — waiting for agent"**, never "Working".
- A hook `stop` event is **"Turn finished"**, never "Ticket complete".
- A dependency-blocked ticket is **"Waiting on DEMO-13"**, never implying work
  started.
- `EmptyState.headline` / `AuditEvent.summary` are server-supplied copy —
  rendered verbatim, never re-derived locally.

## Known gaps

- No screenshot from a real browser is checked into this ticket: this
  environment has no Chromium/Playwright browser binaries installed, and
  adding `@playwright/test` as a dependency just for one smoke screenshot felt
  like more footprint than the ticket warranted. Verified instead with the
  full jsdom/Testing-Library suite (which does mount and interact with real
  React trees) plus a manual `vite build` + `vite preview` + `curl` check that
  the built bundle serves and loads.
- `npm audit` reports vulnerabilities in `esbuild`/`vite`'s dev server (a
  known dev-only advisory about cross-origin requests to the Vite dev
  server). Not fixed here because the fix is a breaking Vite major-version
  bump; low risk for a loopback-only local dev tool per this project's own
  "V1 binds loopback" posture.
- Messages screen is an honest stub ("Messages UI ships in T-189") rather than
  a fixture-only mockup, since the ticket's acceptance list names Overview /
  Tickets / Agents / Activity / detail panel / connect dialog / master panel,
  not Messages.
