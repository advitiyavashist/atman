# The atman app

One screen that answers four questions, top to bottom: what are we trying to
do, what is happening now, what is stuck, what do I need to decide. Talk to a
seat beside it. The design is `docs/design/app-v3.md` (approved in T-1485,
built in T-1486); the clickable mockup it was built from is
`ui/mockups/app-v3.html`, and the screenshots of the real thing are in
`docs/design/app-v3/build/`.

A React + TypeScript app that holds **no board data of its own**. Every
section is a read of the local JSON API that `atm ui` serves under `/api/v1`
(`docs/api/app-v2.md`, schemas in `docs/api/schemas/`). The board and the CLI
stay in Python; this is a window onto them, with six buttons.

## Run it

Two commands, in this order, from the repo root:

```sh
npm install && npm run build -w ui     # once, and after any change to ui/
atm ui --operator <your name>          # then open http://127.0.0.1:8765/app/
```

`--operator` is the name the app acts as. Without it the app is **read-only**
and says so at the top (`Read-only. Start with atm ui --operator <name> to
act.`); every button is disabled with that reason beside it. The name needs
`agents/<name>.json` on this board (`atm join <name>`) and must not be a
harness-run seat.

## What you see

**Top bar.** The wordmark, the project switcher (every board in
`~/.config/atman/board.json`, each with its workload: `1 working · 1 done`,
never `0 open` beside live work), `You are boss`, and the **Details** toggle.
Details is off by default; everything diagnostic (phases, evidence, review
heads, diff commands, receipts, harness badges, usage with its age, transcript
paths, "Board read 2 s ago") is behind it.

**Objective.** The objective sentence, `Done when …`, and `1 of 3 accepted`
with a thin bar. Done-not-accepted never counts as accepted; when there is any
it reads `· 1 done, not accepted`. No objective: `No objective yet. Say what
this board is for.` with `Set objective…` (a sheet with the exact command).

**Now strip.** Four pills, read straight from `plan.counts` and
`needs-you.count`, nothing added client-side: `2 working · 1 needs you · 1
blocked · 1 done`. Zero is `0 blocked` in grey, never hidden. Plan unavailable:
`Board unavailable`.

**Presence strip.** Every registered seat: a dot, its name, and what it is
doing now in a few words (the running ticket's last note, first clause, never
generated). Six visuals, six states: working (solid green), idle (grey ring,
`idle 2 h`), host offline (ring with a slash, the API's `adapter_reason` on
hover), limited (amber, `limited until 17:40`), logged out (amber ring),
unknown (dotted ring with `?`, a seat the board never saw). Clicking a seat
opens Talk with it.

**Sections**, in a fixed order, each collapsing to one line when empty:

| Section | Cards | Buttons |
|---|---|---|
| Needs you | tickets in review (`worker finished it 5 min ago. Waiting for your accept.` with `Review head 3f2a1c0 · <the seat's notes>`), questions (`worker asked 20 min ago about T-001: "…"`), stuck posts, limited / logged-out / stopped seats holding work, escalated automated nodes. Four filter chips: All · Mentions · Accept · Blocked; filtering never changes a count | **Accept 3f2a1c0** · Reject… · Message · Answer · Reassign… · Start… · Open |
| Working | `worker has it, running for 12 min.` / `…last update 40 min ago.` / `…but has been silent for 2 h.` / `Posted to the board 8 min ago. Nobody has picked it up.` Empty: `Nobody is working. Assign a ticket or start an agent.` with **Assign…** then `Start an agent…` | Message |
| Blocked | `Blocked by worker 40 min ago: "<reason>"`, `worker is limited until 17:40. Nothing runs on it until then.`, `worker has no live session.`, `worker is logged out. Run atm harness auth worker.`, `On hold: …` | Answer · Reassign… · Message |
| Up next | `Nobody has it. Ready to start.` / `Nobody has it. Waits on T-001, which is in review.` / `Waits on T-001, which is done but not accepted.` / `Reserved for worker.` | Assign… · Message |
| Done (collapsed) | `Accepted by boss 2 h ago on 3f2a1c0.` with the proof line; `Marked done by worker 2 h ago. Not accepted yet.`; an accept not bound to a review head shows the T-1459 sentence in full | — |

Exactly one sentence per card, in plain words. Clicking a card (or `Open ›`)
opens its **thread panel**: the sentence, the same buttons, every message about
the ticket with its own composer, the ticket text, what it waits on, the review
(submitted by, the full 40-character head, branch, verdicts with *superseded*
and *not bound to the current review head* marked), the acceptance proof, and
Details (runs with `unknown` tokens where none were recorded, usage with its
age, the copyable diff command). `#T-001` in the URL opens it; `Esc` closes
it; at 1440 px it is a resizable side panel, at 390 px a full-screen sheet.

**Talk.** `Talk to [worker ▾]`: the board's chosen lead (`Set as lead` records
one with `POST /api/v1/lead`), else the one running seat, else **Everyone on
the board** (`No agent is running. Messages land on the board; the next agent
that wakes reads them.`). Never an idle seat picked for you. The state line is
the seat's record: `running T-002 for 12 min`, `idle since 2 h`, `limited until
17:40`, `not running · host offline`. The composer posts as the operator
(`POST /msg`): **Enter sends, Shift+Enter adds a line**, nothing is sent during
an IME composition, `@seat` and `#T-001` autocomplete (Enter picks, never
sends), `about [T-002 ▾]` defaults to the ticket the seat is running, and an
unsent draft is kept per (board, recipient, ticket) in the browser.

## The buttons

Every verb is one real `<button>`. A trailing `…` means a dialog asks for what
the verb needs. A disabled button always has its reason as visible text beside
it (`You wrote this, so you can't accept it.`, `Nothing was submitted for review
yet.`, `Pick a seat first.`, the read-only line). After a verb runs, the row is
replaced by one result line built from **what the API returned**, not the
dialog's inputs: `Accepted · by boss · 3f2a1c0`, `Rejected · by boss · 3f2a1c0
· back with worker`, `Assigned · to worker · by boss`. It stays until the next
board read redraws the card.

| Button | Calls | Gate |
|---|---|---|
| Accept 3f2a1c0 | `POST /api/v1/ticket/T-001/accept {sha, notes}` — `atm accept` | `accept_ticket` → `review_verdict.apply(require_full=True)`: operator ≠ author, the full 40-character submitted head, notes required. Before posting, the app re-reads the ticket; a changed head replaces the button with `Changed since you opened this: new head 9c1d2e3` and **Review again** (nothing is posted). The poll does the same while the dialog is open |
| Reject… | `POST /api/v1/ticket/T-001/reject {sha, reason}` — `atm reject` | same binding; the ticket goes back to its author as claimed |
| Assign… / Reassign… | `POST /api/v1/ticket/T-004/assign {owner, notes}` — `atm assign --owner` | a registered seat, chosen by you; a limited or logged-out seat is shown with its state and a warning, never hidden |
| Message / Answer / Send | `POST /msg` | existing; the server fixes `from` to the operator |
| Set as lead | `POST /api/v1/lead` | existing |
| Start an agent… / Set objective… / Create ticket… | nothing — a sheet with the exact command and Copy | a spawn carries your credentials; the app never launches a process |

A refusal comes back in the dialog in the server's own sentence, with the
button re-enabled; nothing is retried on its own. The gate is the CLI's
function in every case, so the app can refuse nothing the CLI allows and allow
nothing it refuses.

## What this app will not do

- **Never invent a provider, a token count, a percentage, a time or an accept.**
  A value the board never recorded renders as the shorter sentence, `unknown`
  or *not recorded* — never as a number, never as a fresh-looking one.
- **Never pick a lead for you**, never prefill a ticket that is not on this
  board, never offer "accept latest" or an accept without notes.
- **No second network peer.** `atm ui` on loopback and nothing else: no
  analytics, no web font, no CDN. The refusal is in `api/client.ts` and tested.
- **Colour never carries a state on its own.** Every state has a word, and the
  presence dots differ in shape as well as colour.
- **Records are only ever shown under the project they were read from.** A
  switch withholds the previous board's payload at once; a failed refresh of
  the same screen keeps the last good data and says the read failed.

## Develop it

```sh
atm ui --dev-origin http://localhost:5173        # tell the API about the dev server
npm run dev -w ui                                # then open http://localhost:5173/
```

On the dev server the write token comes from `GET /api/v1/session`; `POST
/msg` is same-origin only, so the composer works from the served bundle at
`/app/`. In dev every response is validated against its schema in
`docs/api/schemas/` and a violation is printed once per route; that check and
ajv are absent from a production build.

## Test it

```sh
npm test -w ui          # vitest
npm run build -w ui     # tsc -b && vite build — also the type-check gate
```

| file | what it holds to account |
|---|---|
| `v3-model.test.ts` | the spec's tables as pure functions: one sentence per card (§5.3), six presence states (§5.8), buckets, the four counts, progress, the needs-you filters, who the operator talks to |
| `v3-states.test.tsx` | every §7 state mounted: empty, fresh, working, needs-you (filters, Accept dialog with the full head), blocked, accepted (proof), offline (limited seat, unknown seat), self-review (disabled with the reason), read-only, Details, the Done fold |
| `v3-actions.test.tsx` | Accept, Reject, Assign end to end: what is posted, the result line from the response, a refusal in the API's words, the stale-head guard (re-read and poll), the result line's lifetime, Message / Answer |
| `v3-talk.test.tsx` | Enter / Shift+Enter / IME, drafts per ticket, `@` and `#` autocomplete that never sends, ticket badges only for tickets on the board, the panel's thread and composer, the recipient rule, Set as lead |
| `mapping.test.ts` | the honesty rules as pure functions: blocker chips, accept states, usage age, unknown harness, tokens, receipts, review head, acceptance proof (T-1459) |
| `client.test.ts` · `writes.test.ts` | where the app may talk (loopback only), what it sends, what it refuses to send |
| `polling.test.tsx` · `blanking.test.tsx` | a failed refresh keeps the last good read; a project switch never shows the other board's records; a payload the contract forbids never blanks the page |
| `fixtures.test.ts` | the fixture generator itself |

The fixtures are generated from the schemas (`tests/ui/support/schema.ts`);
the §7 boards are `tests/ui/support/v3.ts`. `npm test` starts no browser. The
screenshots are `scripts/app_v3_shots.py`: it seeds one throwaway board per
state, serves it with the real `atm ui`, and captures 1440 and 390 with
Playwright, refusing a state whose page scrolls sideways.

The Python side of the same contract is `tests/test_t1486_app_actions.py`
(the three write routes, `thread?re=`, the needs-you kinds) and
`tests/test_t1103_app_api.py` (every route against its schema).
