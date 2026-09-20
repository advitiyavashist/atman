# The atman app

Chat with the project's lead, beside a live execution plan.

A React + TypeScript app that holds **no board data of its own**. Every screen
is a read of the local JSON API that `atm ui` serves under `/api/v1`
(`docs/api/app-v2.md`, schemas in `docs/api/schemas/`). The board and the CLI
stay in Python; this is a window onto them.

## Run it

Two commands, in this order, from the repo root:

```sh
npm install && npm run build -w ui     # once, and after any change to ui/
atm ui                                 # then open http://127.0.0.1:8765/app/
```

`atm ui` serves the built bundle at `/app/` on the same origin as the API, which
is what makes the composer work: it inserts the per-launch write token into the
page as a meta tag, and `POST /msg` only accepts a request whose `Origin` is its
own `Host`. Nothing binds to anything but loopback.

### What you should see

**The shell.** A sidebar with the project switcher (every board in
`~/.config/atman/board.json`, the started one first, each with its open count
and its lead) and five views: Lead, Plan, Needs you, Fleet, Runs. At 1440px the
chat, the plan and the drill-down read at once. At 390px the sidebar becomes a
top bar, chat and the centre view become two tabs, and the drill-down is a
full-screen sheet. The URL hash is `#<view>` or `#<view>/T-123`, so a step you
are looking at can be reloaded or handed to someone else.

**Lead** — the chat, on the left of every view.

- A title pill: `lead · seat@project`, the seat's harness, and whether it
  **takes mid-run messages** or **answers on its next turn** (the line comes
  from the same table `atm steer` uses).
- A status strip: liveness, the running ticket and how long it has been going,
  when output was last seen, any limit, auth, and the harness usage reading
  **with its age**. A reading that was never taken says `share unknown` and
  `age unknown` — never a number, never a fresh-looking one.
- Posts, oldest first, opening on the newest. Each shows `seat@project`, a
  harness badge (`unknown · not recorded` when the board has no harness stamped
  at post time — it is never filled in from anything else), your own local
  time, a copy button, and its **delivery receipts**, labelled as receipts:
  *posted · inbox read · wake confirmed*. A receipt is never an agent saying
  yes, and a word that reads like one is refused on screen.
- **Load older messages** pages back through the whole live log, not the
  snapshot's newest 40.
- When the lead cannot answer — limited, logged out, at quota, no live
  session — a plain line says so, with the command that fixes it, and says your
  message still lands on the board.
- The composer posts **as the operator**. Until `atm ui --operator <name>`
  exists (T-1104, #265), no operator is configured: the app says *read-only: no
  operator configured* and shows the exact command instead of a Send button
  that could only fail.
- On a project with no lead picked, you get a **picker** of every registered
  seat with its harness and capability line — and no guess. The app never falls
  back to the master or the CoS.

**Plan** — the centre, from `/api/v1/plan`. The objective and its exit
criterion, the counts, then every step in dependency order with its owner as
`seat@project`, its state, a *running* chip when a run is open on it, and a
typed **blocker chip** with the copyable command that clears it:

| chip | what it means |
|---|---|
| dependency not accepted | the dep is finished, but nobody accepted it |
| dependency still open | the dep has not finished (or is not on this board) |
| seat limited | the owner is limited, with the reset time |
| seat offline | the owner has no live session |
| auth | the owner's harness is logged out |
| hold · capture · blocked | recorded waits |
| done, not accepted | this step is finished with no accept |

Work that is done without a structured accept **never renders as accepted**. It
reads *done, not accepted*; a release override reads *done, released by
override (not accepted)*. The label is derived from the record, so a
`status_label` that claimed otherwise could not put the word on screen — and
the step says that the label was refused.

**Drill-down** — click a step (or a ticket id anywhere), from
`/api/v1/ticket/{id}`. Runs with seat, harness, role, elapsed time and tokens
(`unknown` when the board never recorded them — never 0); usage readings each
with their age; the **full 40-character review head**, because an accept is
bound to exactly that sha; verdicts, with *superseded* and *not bound to the
current review head* marked, since a stale accept is not an accept of this
work; the handoff; the messages about it; the steers with their receipts;
branch, commit, PR; and the **copyable diff command** — a read route runs no
git, so the app hands you the command instead of a diff it cannot produce.
*back to plan* closes it.

**Needs you** — the read-only queue. Messages that named you with no reply,
prose that says DECIDE, `stuck:` posts older than an hour, escalated automated
nodes. Every item is *asked (unstructured)*: the board holds no decision
records yet, so prose containing the word "ruling" is still an ask, never a
ruling. Answering happens in the chat.

**Fleet** — this project's seats: state, harness, lifecycle, wake mode,
reachability, limit, auth, with the recovery command where there is one. The
screen says in so many words that **the harness column is not a record**: this
route still fills the field in for a seat with no workforce entry, so an
unrecorded harness can appear here as a provider name, and the app has no way
to tell the two apart (T-1106 / #267 is the fix). The chat's per-post badge is
the honest one.

**Runs** — seat runs grouped by ticket, with elapsed time and token counts, and
`unknown` wherever a count was never recorded.

Anything the board does not hold is rendered, visibly, as `unknown` or *not
recorded*, in italics. A read that fails says so where the data would have
been: a screen of nothing would look exactly like an empty board, and those are
different things. When a **refresh** fails after a good read, the last good data
stays on screen and the line at the top names the read that failed — wiping a
screen someone is reading would be both less useful and less honest.

Keyboard: the first tab stop in the chat is **Skip to the composer**, because a
hundred-post thread is a few hundred controls deep. Focus is visible on
everything, and every state has a word as well as a colour.

## Develop it

```sh
atm ui --dev-origin http://localhost:5173        # tell the API about the dev server
npm run dev -w ui                                # then open http://localhost:5173/
```

The dev server is a different origin from the API, so:

- the write token comes from `GET /api/v1/session` with `X-Atman-Client: app`,
  which forces a CORS preflight that only the app's own origin or a
  `--dev-origin` passes;
- point the app at a port other than the default 8765 with
  `?api=http://127.0.0.1:<port>`. A value that is not a loopback origin is
  refused, with the reason on screen, and no request is made;
- **the composer cannot post from the dev server.** `POST /msg` sends no CORS
  headers and refuses an `Origin` that is not its `Host`, and the API adds no
  message route of its own. The app says this instead of showing a network
  error. Post from the bundle at `/app/`.

In dev, every response is validated against its schema in
`docs/api/schemas/` and a violation is printed to the console once per route.
That check and ajv are **absent from a production build**: they sit behind
`import.meta.env.DEV` and a dynamic import, so `grep -c "schemas.atman.invalid"
dist/assets/*.js` is 0 after `npm run build -w ui`.

## Test it

```sh
npm test -w ui          # vitest: 143 tests, 10 files
npm run build -w ui     # tsc -b && vite build — also the type-check gate
```

| file | what it holds to account |
|---|---|
| `mapping.test.ts` | every honesty rule as a pure function: blocker chips, accept states, usage age, unknown harness, tokens, receipts, review head, ticket links, verdict shapes, one phrase per step |
| `client.test.ts` | where the app may talk (loopback only), what it sends, what it refuses to send |
| `writes.test.ts` | the `/api/v1/lead` **request** body against `lead-request.json`, and both refusal paths |
| `polling.test.ts` | a failed refresh keeps the last good read and the freshness line names it |
| `blanking.test.tsx` | the payload shapes and the shell throw that used to leave an empty page |
| `fixtures.test.ts` | the fixture generator itself, including that it refuses a field the schema lacks |
| `screen-*.test.tsx` | one file per screen, mounted: chat, plan, drill-down, and needs-you / fleet / runs / shell |

The fixtures are **generated from the schemas** (`tests/ui/support/schema.ts`):
each test's payload starts as a minimal instance of the real schema file, the
case under test is merged on top, and the result is validated with ajv. A field
that leaves the contract leaves every fixture at once, and a field a test
invents fails there instead of rendering as `undefined`. The generator's
defaults are the honest ones — a nullable number is `null`, a stamp is `""` —
and the schema's own `if`/`then` rules are applied, which is how an unaged
usage reading keeps `age: "age unknown"` and a null token count keeps
`tokens_label: "unknown"`.

`npm test` does not start a browser. The real-data check is the two commands at
the top of this file, against a throwaway board.

## What this app will not do

- **Never invent a provider, a token count, a percentage or an accept.** Every
  one of those has a test.
- **No writes beyond two.** The composer (`POST /msg`, as the operator) and the
  lead picker (`POST /api/v1/lead`). There is no accept, reject, merge, done,
  claim, assign, dispatch, spawn or objective-set route in the API, in any
  phase; the app shows the copyable command instead.
- **No second network peer.** `atm ui` on loopback, and nothing else: no
  analytics, no web font, no CDN, no external script. The refusal is in
  `api/client.ts` and tested.
- **No polish that outruns the data.** Colour never carries a state on its own;
  every state also has a word.

## Known limits

- **No operator until #265.** `atm ui --operator <name>` does not exist yet, so
  posting is off and the app says so. The read screens are all live.
- **`agent_map` rows are not contract.** `board.json` types only
  `agent_map.groups`, so the Runs screen treats every field of a row as
  possibly absent. One field bit: a run's `verdict` is `{kind, sha}` there and a
  plain string on `ticket.json`. It goes through `verdictChip` now, rendering
  the raw object having blanked the whole page once.
- **A blank page is treated as a bug, everywhere.** `main.tsx` mounts `Root`,
  which is the app inside a boundary, each column has its own boundary, and the
  shell checks a list's shape before walking it. `tests/ui/blanking.test.tsx`
  holds the shapes that used to do it: a `nodes` that is not a list, a snapshot
  with no `agents`, and a throw in the shell above every column boundary.
- **`agents[].harness` still carries the snapshot's default, so Fleet does not
  vouch for it.** A seat with no workforce entry arrives already wearing a
  provider's name and there is no "was it recorded" flag on this route, so the
  screen says that in the caption rather than dressing the value up. T-1106
  (#267) fixes the default; `post.harness.recorded` already makes the chat's
  badge honest.
- **The plan is a list, not a graph.** Layers and dependency order come from
  the API and read top-down. The graph and column layouts are a later phase.
- **The thread polls.** Every read refreshes on a timer; there is no event
  route, and token streaming would mean reading harness transcripts, which this
  product does not do.
