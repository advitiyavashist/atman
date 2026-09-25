# Atman app v3: the one-screen operator view

**Ticket:** T-1485 (design, 1 of 3) · **Status:** SPEC + clickable mockup, revision 2, no app code · **Base:** `main@d84efec` · **Mockup:** `ui/mockups/app-v3.html` · **Screenshots:** `docs/design/app-v3/` · **Next:** T-1486 builds this only after the operator approves.

**Revision 2** (CEO, 2026-09-25, after a study of another human+agent workspace) adds seven things and nothing else: a presence strip (§5.8), the ticket thread panel with ticket badges in chat (§5.9), Needs-you filters (§5.10), the one-verb button model with result and reason lines (§6.1), the stale-head guard (§6.2), `@seat` / `#T-001` autocomplete with drafts kept per ticket (§5.6), and Done collapsed by default (§5.4). What it does **not** adopt is in §10.

The operator's verdict on the shipped `/app/` was "doesn't look very good and usable". This document says what replaces it. It is written so that the build ticket can be checked against it line by line: every label's copy, every state, every button and what it calls.

## 1. The four questions

The screen exists to answer four questions at a glance, in this order, top to bottom:

| # | Question | Where it is answered | In one glance |
|---|---|---|---|
| a | What are we trying to do? | **Objective** bar at the top | The objective sentence, "Done when …", and how far along: `1 of 3 accepted` |
| b | What is happening now? | **Now** strip (four counts) and the **Working** section | `2 working · 1 needs you · 1 blocked · 1 done`; each working card says who has it and for how long |
| c | What is stuck? | **Blocked** section | Each card says why in the owner's words and what would unblock it |
| d | What do I need to decide? | **Needs you** section, first when non-empty | Accept or reject a review head, answer a question, restart a stopped agent. Each item carries its buttons |

Everything else (paths, transcripts, receipts, limits, auth, usage, phase names) is diagnostic and lives behind one **Details** toggle. Off by default.

### What v3 removes from v2

- The 13-counter row. v3 shows four counts. The other phases (posted, reserved, ready, waiting, capture, hold, discarded, done-unverified) stay visible as words on cards and in Details, never as a row of zeros.
- The auto-picked lead. v3 never picks an idle seat and never prefills "Tell worker…" for a seat that is not running. See §4.
- Command-with-Copy as the only action. v3 has real buttons for assign, accept, reject, message and answer. Commands appear only inside Details and for the one action the app does not run itself (starting an agent, §6.4).
- Sidebar views (Lead / Plan / Needs you / Fleet / Runs). One screen; the plan, needs-you queue, team and runs are sections of it, not tabs. Ticket drill-down is a side panel with that ticket's thread (§5.9).
- The corner clock, the "start of the thread" header, the "board read 1s ago" line in the header (moved to Details as "Board read 2 s ago").
- Jargon on the front: "unknown share unknown · age unknown", "run none · last output not recorded · limit none · auth Not checked", "DELIVERY RECEIPT", "ready first 1 steps", "after layer 1 1 steps", "dep T-001 still open", "@?".

### Invariants v3 keeps (T-1459, T-944, T-992, T-1103)

- **Counts agree with the board.** The four counts are read straight from `/api/v1/plan.counts` and `/api/v1/needs-you.count`; nothing is added client-side. If the plan is unavailable the strip says `Board unavailable` and shows no numbers.
- **Never "no proof" beside "Accepted by".** An accepted card shows `Accepted by @seat on <sha7>`; the proof line is the sounding sentence when there is one, otherwise the structured accept record. An accept that is not bound to a review head shows the T-1459 sentence ("accept by @x on <sha7> is not bound to a review head …") and the card stays in Done as **done, not accepted**.
- **Receipts are receipts.** Delivery badges read `posted`, `inbox read`, `wake confirmed`, `queued`. They never say acknowledged, understood or on it. They sit in Details.
- **Usage has an age or is unknown.** A usage line reads `usage 62 %, read 4 min ago` or `usage not read`. Never a number without an age, never `0`.
- **Harness is never invented.** An unrecorded harness renders as `harness not recorded`, only in Details.
- **Read paths stay read-only.** Every section is rendered from the existing GET routes. No GET writes, runs `git` or `ps`.
- **Author can never accept.** The Accept button is disabled with its reason when the operator is the ticket's owner. The server (`review_verdict.apply`) still refuses; the button is a courtesy, not the gate.
- **Accept binds to the exact review head.** The Accept dialog shows the full 40-character sha the seat submitted and posts that sha. There is no "accept latest".

## 2. Layout

### 1440 px (desktop)

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ atman   [demo ▾]                                   You are boss    ○ Details          │  top bar, 48 px
├──────────────────────────────────────────────────────────────────────────────────────┤
│ Ship a small stats API with a screen on top                              1 of 3 accepted │  objective
│ Done when the screen shows live stats from the API, accepted by a second seat  ━━━━━━━━━  │
├──────────────────────────────────────────────────────────┬───────────────────────────┤
│  [2 working] [1 needs you] [1 blocked] [1 done]          │  Talk to                  │
│  ● worker · writing tests for T-002   ○ astra · idle 2 h │  worker · running T-002   │
│  NEEDS YOU (1)   All · Mentions · Accept · Blocked       │  ─────────────────────    │
│  ┌ T-001 Sample: design the data model ────────────────┐ │  boss   9:31 PM           │
│  │ worker finished it 5 min ago. Waiting for your accept.│ │  hi                       │
│  │ Review head 3f2a1c0 · 2 files · tests pass (per note)│ │  worker  9:40 PM          │
│  │ [Accept 3f2a1c0]  [Reject…]  [Message]  Open ›      │ │  claimed T-001; schema …  │
│  └──────────────────────────────────────────────────────┘ │                           │
│  WORKING (2)                                             │                           │
│  ┌ T-002 … worker has it, running for 12 min.  Open › ─┐ │                           │
│  BLOCKED (1)                                             │                           │
│  ┌ T-003 … Blocked by worker 40 min ago: "needs…" ─────┐ │  ┌──────────────────────┐ │
│  UP NEXT (1)                                             │  │ Message worker…       │ │
│  ┌ T-004 … Nobody has it. Waits on T-002. [Assign…] ───┐ │  └──────────────────────┘ │
│  ▸ DONE (1)                                  collapsed   │                           │
│                                                          │  about [T-002 ▾]   [Send]  │
└──────────────────────────────────────────────────────────┴───────────────────────────┘
      main column, max 880 px, sections stacked           right column 360 px, sticky
```

- **Top bar** (48 px): wordmark, project switcher, `You are <operator>` (or `Read-only: start atm ui --operator <name> to act`), the Details toggle. Nothing else.
- **Objective** (two lines): objective text at 20 px / 600; "Done when …" at 14 px muted; on the right `1 of 3 accepted` with a thin progress bar. If the objective has no exit criterion: `No "done when" set · atm objective --exit "…"` in amber.
- **Now strip**: four pills. Each is a link that scrolls to its section. Zero renders as `0 blocked` in muted grey, never hidden and never highlighted.
- **Presence strip** (one line under the pills): every registered seat as a dot, its name and what it is doing now in a few words (§5.8). Clicking a seat opens Talk with it.
- **Sections**, in fixed order: Needs you · Working · Blocked · Up next · Done. A section with nothing in it collapses to its one-line empty copy (§5). The order never changes, so the eye learns where to look. Needs you carries four small filters in its header (§5.10). Done is collapsed by default (§5.4).
- **Ticket card**: id + title on one line (title 15 px / 600), then exactly one sentence in plain words (§5.3), then a row of buttons that apply to that card (§6). Clicking the card anywhere outside a button opens that ticket's thread panel (§5.9); `Open ›` does the same.
- **Talk column** (360 px, sticky): who you are talking to (§4), the thread with that party (oldest first, newest visible), the composer (Enter sends, Shift+Enter adds a new line, §5.6). No status strip; the seat's state is one line under the name.
- **Ticket panel** (side panel, 480 px by default, resizable by dragging its left edge between 360 and 720 px, slides over the Talk column): one ticket's thread. Title, the one sentence, the card's buttons, then every message about that ticket oldest first with a composer scoped to it, then the ticket body, review head and verdicts, acceptance proof and a Details block (§5.9).

### 390 px (phone)

- Top bar keeps wordmark, project, Details toggle. `You are boss` moves under the objective.
- Objective stays two lines; progress becomes `1/3 accepted` on the second line.
- Now strip: four pills in one row, compact (`2 working`), wrapping to two rows below 360 px.
- A segmented control **Board | Talk** sits under the strip. Board is the stacked sections; Talk is the Talk column full width. The segment shows an unread dot when the other side has something new (Talk: a new message; Board: a new needs-you item).
- Presence strip wraps to one seat per line.
- Needs-you filters stay in the header as four small chips; they wrap under the heading below 360 px.
- Cards are the same cards; the button row wraps. Tapping a card opens the ticket panel as a full-screen sheet with a `‹ Back` button; no resize handle.
- Dialogs (Accept, Reject, Assign) are bottom sheets.

Both widths are in `docs/design/app-v3/` for every state.

## 3. Data: which route feeds what

Everything on screen is read from routes that exist today, joined client-side. No new read route is needed for v3.

| Element | Route and field |
|---|---|
| Project switcher | `GET /api/v1/projects` |
| `You are boss` / read-only banner | `GET /api/v1/board` → `app.operator`, `app.operator_note` |
| Objective, done-when, missing-exit flag | `GET /api/v1/plan` → `objective.text`, `objective.exit_criterion`, `objective.exit_missing` |
| `1 of 3 accepted` | `plan.counts.done` over `len(plan.nodes)` minus `plan.counts.discarded`. Done-not-accepted (`node.unverified`) does not count as accepted |
| Four counts | `working` = `plan.counts.working`; `needs you` = `needs-you.count` + review items where operator ≠ owner (see below); `blocked` = `plan.counts.blocked`; `done` = `plan.counts.done` (accepted only; `done_unverified` shown as a suffix `· 1 not accepted` when non-zero) |
| Needs you items | `GET /api/v1/needs-you.items` (asked questions, stuck > 1 h, escalated nodes) plus every node with `phase == "review"` whose `review.latest` has no verdict from the operator. Sorted oldest first |
| Working cards | nodes with `phase in (working, posted)`, sentence from `who`, `running.elapsed_s`, `since_update_h` |
| Blocked cards | nodes with `phase == "blocked"` or `wait.kind in (blocked, hold, capture)` or `blockers` naming `seat_limited`, `seat_offline`, `auth`; sentence from `last_note` and `wait.text` |
| Up next cards | nodes with `phase in (ready, reserved, waiting)`; `wait.on` for "Waits on T-001" |
| Done cards | nodes with `status == done`; `accepted`, `unverified`, `review.latest.by`, `review.latest.sha` |
| Presence strip | `board.agents[]`: `state` (working / idle), `reachable` + `adapter_state` + `adapter_reason` (host offline and why), `seen_h` (unknown when null), `limit` / `limit_until`, `auth.state`; the doing-now words are `plan.nodes[].last_note.text` of the seat's `ticket`, shortened to the first clause |
| Talk: who | §4, from `board.agents[]` (`state`, `running.ticket`, `elapsed_s`) and `board.app.lead` |
| Talk: thread | `GET /api/v1/thread?with=<seat>` or the board broadcast when talking to everyone |
| Ticket panel | `GET /api/v1/ticket/<id>` for the ticket; `GET /api/v1/thread?re=<id>` (build adds the `re` filter to the existing thread route, read-only) for the messages about it |
| Ticket badges in chat | every `#T-001` / `T-001` token in a message body is looked up in `plan.nodes` by id; the badge shows `phase` and `owner`. An id that is not on this board renders as plain text, never as a badge |
| Details block on a card | `node.evidence`, `node.phase`, `node.wait.cmd`, `ticket.artifact`, `ticket.diff_cmd`, `ticket.review.verdicts[]` |
| Details block on a seat | `agents[]`: `harness`, `detail` (transcript path), `limit`, `limit_until`, `auth.label`, `usage.text` + `usage.age`, `wake_mode`, delivery receipts on each message |
| `Board read 2 s ago` (Details) | `plan.generated` vs now |

## 4. Who the operator talks to

The rule the brief sets: **the lead is the operator or a real running agent, never an idle seat picked automatically.**

1. If `board.app.lead` is set (the operator chose one with `POST /api/v1/lead`) and that seat is registered, Talk opens with it. Its state line is truthful: `running T-002 for 12 min`, `idle since 2 h`, `offline`, `limited until 17:40`.
2. Otherwise, if exactly one seat is running (`state == working`), Talk opens with it and says `worker · running T-002 for 12 min`. It is a real running agent; the choice is stated on screen and can be changed.
3. Otherwise Talk opens with **Everyone on the board**. The state line says `No agent is running. Messages land on the board; the next agent that wakes reads them.` A `Start an agent…` button sits beside it (§6.4).
4. The recipient is always a control (`Talk to [worker ▾]`) listing Everyone plus every registered seat with its state. Picking a seat is per session and is not written anywhere; `Set as lead` inside the picker calls `POST /api/v1/lead`.

Nothing in Talk says "unknown" unless the board's own record is unknown, and then only in Details.

## 5. Copy for every label

Plain words, sentence case, no abbreviations the operator did not type. Times are relative (`5 min ago`, `2 h ago`) and switch to a date after 24 h. Seat names are shown bare (`worker`), with `@` only inside proof lines that mirror the CLI record.

### 5.1 Top bar and objective

| Where | Copy |
|---|---|
| Operator | `You are boss` |
| No operator | `Read-only. Start with atm ui --operator <name> to act.` |
| Details toggle | `Details` (switch; on = `Details on`) |
| Objective line 2 | `Done when <exit_criterion>` |
| Missing exit | `No "done when" set yet.` + Details: `atm objective --exit "…"` |
| Objective closed | `Objective closed <date>.` |
| No objective | `No objective yet. Say what this board is for.` + button `Set objective…` (§6.6) |
| Progress | `1 of 3 accepted` · when done-not-accepted exists: `1 of 3 accepted · 1 done, not accepted` |

### 5.2 Now strip

`2 working` · `1 needs you` · `1 blocked` · `1 done`. Singular is not special-cased: `1 working` reads fine. Zero: `0 blocked`. Plan unavailable: `Board unavailable` + the error in Details.

### 5.3 Ticket card sentence (exactly one)

| State | Sentence |
|---|---|
| Working | `worker has it, running for 12 min.` |
| Working, no live run but claimed | `worker has it, last update 40 min ago.` |
| Working, stale (> 90 min silent) | `worker has it but has been silent for 2 h.` |
| Posted, nobody picked up | `Posted to the board 8 min ago. Nobody has picked it up.` |
| Reserved | `Reserved for worker. Starts when worker wakes.` |
| In review | `worker finished it 5 min ago. Waiting for your accept.` |
| In review, operator is author | `You finished it 5 min ago. Another seat has to accept it.` |
| In review, verdict by someone else pending merge | `Accepted by astra 1 h ago. Waiting for merge.` |
| Rejected, back with author | `Rejected by boss 10 min ago: "<reason>". Back with worker.` |
| Ready | `Nobody has it. Ready to start.` |
| Waiting on deps | `Nobody has it. Waits on T-001.` / `Waits on T-001, which is done but not accepted.` |
| Hold | `On hold: <reason>.` |
| Capture | `Waiting for capture: <reason>.` |
| Blocked | `Blocked by worker 40 min ago: "<reason>".` |
| Blocked, seat limited | `worker is limited until 17:40. Nothing runs on it until then.` |
| Blocked, seat offline | `worker has no live session. It picks this up when started.` |
| Blocked, auth | `worker is logged out. Run atm harness auth worker.` |
| Done, accepted | `Accepted by boss 2 h ago on 3f2a1c0.` |
| Done, not accepted | `Marked done by worker 2 h ago. Not accepted yet.` |
| Done, accept not bound | `accept by @boss on 3f2a1c0 is not bound to a review head: run atm reopen T-001 --notes "...", then claim, then atm review, then atm accept --sha <new head>` (verbatim from the API) |
| Discarded | `Discarded <date>.` |

### 5.4 Section headers and empty copy

| Section | Header | Empty |
|---|---|---|
| Needs you | `Needs you (1)` | `Nothing needs you right now.` |
| Working | `Working (2)` | `Nobody is working. Assign a ticket or start an agent.` Buttons in this order: **Assign…** (primary, filled) then `Start an agent…` (quiet, text-only). Assigning is the action the app runs itself; starting an agent is a copied command (§6.4), so it never outranks Assign |
| Blocked | `Blocked (1)` | `Nothing is blocked.` |
| Up next | `Up next (2)` | `Nothing is queued.` |
| Done | `▸ Done (1)` collapsed by default; click the header to expand to `▾ Done (1)`. Collapsed, the header alone stays visible so the count is never hidden. The choice is kept per browser (`localStorage`), not on the board | `Nothing accepted yet.` |
| Whole board empty | (sections hidden) | `This board has no tickets. Add work with atm plan or atm create.` + `Create ticket…` (§6.6) |

### 5.5 Needs-you item kinds

| Kind | Line | Buttons |
|---|---|---|
| Review waiting | ticket card with `Waiting for your accept.` and a second line `Review head 3f2a1c0 · <notes from the review>` | Accept · Reject… · Message · Open |
| Question asked (`DECIDE`, `@boss`, addressed message) | `worker asked 20 min ago: "<text>"` | Answer · Open T-002 |
| Stuck > 1 h | `worker has been stuck on T-002 for 1 h: "<text>"` | Answer · Reassign… · Open |
| Escalated automated node | `Automated step T-009 escalated: <reason>` | Keep · Remove |
| Agent stopped / limited / logged out (from `board.agents`) | `worker stopped 30 min ago. T-002 is waiting on it.` / `worker is limited until 17:40.` / `worker is logged out.` | Start… · Reassign… |

### 5.6 Talk column

| Where | Copy |
|---|---|
| Header | `Talk to` + recipient control |
| State line, running | `running T-002 for 12 min` |
| State line, idle | `idle since 2 h` |
| State line, offline | `not running` |
| State line, limited | `limited until 17:40` |
| State line, logged out | `logged out · atm harness auth worker` |
| Everyone | `Everyone on the board` + `No agent is running. Messages land on the board; the next agent that wakes reads them.` |
| Cannot answer now (seat) | `worker is not running. Your message is kept; it reads it when it starts.` |
| Composer placeholder | `Message worker…` / `Message everyone…` |
| About | `about [T-002 ▾]` — defaults to the ticket the seat is running, else the first option `no ticket`. Never prefilled with an id that is not on this board |
| Send | `Send` button, kept. **Enter** also sends |
| Keyboard | Enter sends the message. Shift+Enter adds a new line (so does Enter with any other modifier). While an IME composition is active (`KeyboardEvent.isComposing`, or the legacy `keyCode 229`), Enter belongs to the editor and nothing is sent. Empty or whitespace-only text is never sent; Enter just keeps focus in the box. A disabled composer (read-only) ignores Enter |
| Hint under the box | `Enter to send · Shift+Enter for a new line` — one small muted line between the box and the about/Send row |
| `@` autocomplete | Typing `@` opens a small list above the caret with every seat on the board (`board.agents[].name`) plus `everyone`, each with its presence dot and state (§5.8). ↑ ↓ move, Enter or Tab inserts `@worker `, Esc closes. Enter while the list is open picks; it never sends. The list filters as you type |
| `#` autocomplete | Typing `#` opens the same list with tickets (`plan.nodes`): `T-001 · Sample: design the data model · in review · worker`. Picking inserts `#T-001 ` and, when `about` is still `no ticket`, sets `about` to that ticket |
| Drafts | Unsent text is kept per ticket: the key is (board, recipient, about). Switching `about` or the recipient swaps the draft in and out; sending clears it. Kept in the browser (`localStorage`), never posted anywhere. A kept draft shows `Draft` in muted text beside the about control |
| Empty thread | `No messages with worker yet.` |
| Details on: per message | receipts `posted · inbox read · wake confirmed`, harness badge, `Copy` |

### 5.7 Details block (toggle on)

Per card: `phase working · evidence: Unblocked · no reservation … · atm show T-002`. Per review: full sha, branch, PR, `diff: git diff main...<sha>` with the API's note `copy and run it in the repo; the app never runs git on a read`. Per seat: `harness cursor · transcript <path> · last output 4 min ago · usage 62 %, read 4 min ago · auth ok, checked 1 h ago · wake continuous`. Board: `Board read 2 s ago · /path/to/.tickets`.

### 5.8 Presence strip

One line under the four pills: every registered seat, oldest registration first, the operator last as `boss (you)`. Each seat is a dot, the seat name, a middle dot, and what it is doing now in a few words. Clicking a seat opens Talk with it. Hovering a non-working dot shows the reason (also the `title` for keyboard users; the reason repeats in Details).

| State | Dot | Words after the name | Hover reason |
|---|---|---|---|
| Working | solid green | the running ticket's latest update, shortened to the first clause and prefixed with the id: `writing tests for T-002`. No update since the claim: `running T-002, no update yet` | none needed |
| Idle | hollow grey ring | `idle 2 h` (`seen_h`) | `Reachable, nothing claimed. Last seen 2 h ago.` |
| Host offline | grey ring with a slash | `host offline` | the API's `adapter_reason`, e.g. `Adapter offline; directed work will remain queued.` |
| Limited | amber solid | `limited until 17:40` | `Provider quota; resets 17:40. Nothing runs on it until then.` |
| Logged out | amber ring | `logged out` | `atm harness auth worker` |
| Unknown | dotted grey ring with `?` | `unknown` | `No liveness record for this seat. Never seen by this board.` (`seen_h` null and no adapter state) |

Six visuals, six states. The strip never shows one grey dot for "not working": idle, host offline and unknown are different facts and each says why on hover. The words are the API's facts, not a mood: no "thinking", no "on it".

### 5.9 Ticket thread panel

Clicking a card (anywhere that is not a button) or `Open ›` opens the panel for that ticket. On desktop it is a side panel over the Talk column, 480 px wide by default, with a drag handle on its left edge (360 to 720 px, kept per browser). At 390 px it is a full-screen sheet with `‹ Back`. `Esc` closes it; `#T-001` in the URL opens it.

Top to bottom: `T-001 · backend`, the title, the one sentence (§5.3), the same buttons as the card (§6), then **the thread about this ticket**: every message whose `re` is this ticket, oldest first, with its own composer below (recipient = the ticket's owner or Everyone, `about` fixed to this ticket, same keyboard rule, same autocomplete, its own kept draft). Under the thread: Ticket body, Waits on, Review (submitted by, full head, branch, notes, verdicts), Acceptance proof, Details.

**Ticket badges in chat.** Wherever a message in Talk or in the panel mentions a ticket (`#T-001` or a bare `T-001`) the id renders as a small badge: `T-001 · in review · worker` (id, phase word, owner; `nobody` when unowned; done shows `accepted` or `done, not accepted`). Clicking a badge opens that ticket's panel. Ids that are not on this board stay plain text, so the badge itself is a truthful claim that the ticket exists here.

### 5.10 Needs-you filters

Four small chips in the section header: `All (3)` · `Mentions (1)` · `Accept (1)` · `Blocked (1)`. `All` is selected on load; the choice lasts for the session only. Filtering never changes the count in the Now strip or in the header.

| Chip | Shows |
|---|---|
| All | every item, oldest first |
| Mentions | questions and messages that name the operator (`@boss`, `DECIDE`, addressed messages) |
| Accept | tickets in review waiting on the operator's verdict |
| Blocked | stuck items, blocked tickets, and seats that are limited, logged out or stopped with claimed work |

An empty filter says `Nothing under Mentions.` (with the chip's word); the section's own empty copy is only for an empty `All`.

## 6. Actions: buttons and what each calls

### 6.1 The button model

Every action is one verb on one `<button>`: `Accept 3f2a1c0`, `Reject…`, `Assign…`, `Message`, `Answer`, `Send`. A trailing `…` means a dialog asks for what the verb needs (notes, a reason, a seat). Nothing runs on a bare click that the CLI would refuse.

- **After it runs**, the button row is replaced by one result line in the same place, in the past tense with the actor and the proof: `Accepted · by boss · 3f2a1c0`, `Rejected · by boss · 3f2a1c0 · back with worker`, `Assigned · to worker · by boss`, `Sent · to worker · posted`. The line is what the API returned, not what was requested; it stays until the next board read renders the card in its new state (an accepted card then shows in Done with §5.3's sentence).
- **When it is disabled**, the reason is visible text next to the button, not only a tooltip: `You wrote this, so you can't accept it.`, `Nothing was submitted for review yet.`, `Read-only. Start with atm ui --operator <name> to act.`, `Pick a seat first.` A disabled button without a visible reason is a bug.
- **While it runs**, the button reads its verb with a spinner and is disabled; the dialog stays open until the API answers. An error is shown in the dialog in the API's words with the button re-enabled; nothing is retried on its own.

### 6.2 Stale view

Each card remembers the review head it was drawn with. Before `Accept` or `Reject` posts, the app re-reads `GET /api/v1/ticket/<id>`. If `review.latest.sha` differs from the head on screen, nothing is posted. The dialog's primary button is replaced by the line `Changed since you opened this: new head 9c1d2e3 · ` and a `Review again` button, which redraws the card and the dialog with the new head and the seat's new notes; the operator reads them and decides afresh. The same check runs when the board poll brings a new head while a dialog is open. The server's sha binding (§1) stays the real gate; this guard only stops the operator from posting a verdict they did not look at.

### 6.3 Routes

The app calls the local API only. Four write routes are new in T-1486; each is a thin wrapper around the CLI function it names, so the CLI gate and the app gate are one function. All writes need the per-launch token and loopback Host, exactly like `POST /api/v1/lead` today.

| Button | Where | Dialog | Calls | CLI equivalent | Gate |
|---|---|---|---|---|---|
| **Accept 3f2a1c0** | review card, panel | Shows ticket, full sha, review notes, a `Notes` field (required). Primary `Accept 3f2a1c0` | `POST /api/v1/ticket/T-001/accept {sha, notes}` | `atm accept T-001 --sha <40> --notes "…"` | `review_verdict.apply(require_full=True)`: operator ≠ owner; sha equals the submitted review head. Disabled copy, shown beside the button (§6.1): `You wrote this, so you can't accept it.` / `Nothing was submitted for review yet.`. Before posting, the stale-view check (§6.2) |
| **Reject…** | review card, panel | `Reason` field (required). Primary `Reject and return to worker` | `POST /api/v1/ticket/T-001/reject {sha, reason}` | `atm reject T-001 --sha <40> --reason "…"` | same sha binding; returns the ticket to the author as claimed (T-1460) |
| **Assign…** / **Reassign…** | up next card, blocked card, stuck item, panel | Seat list with each seat's state; a `Why` field (optional). Primary `Assign to worker` | `POST /api/v1/ticket/T-004/assign {owner, notes}` | `atm assign T-004 --owner worker --notes "…"` | seat must be registered; a limited or logged-out seat is shown with its state and a warning, not hidden |
| **Message** | any card, panel | No dialog of its own: focuses the composer with `about` set to that ticket and the recipient set to the ticket's owner (or Everyone). Same keyboard rule as the Talk panel (§5.6): Enter sends, Shift+Enter new line, nothing during an IME composition | `POST /msg` (existing; server fixes `from` to the operator, `via: ui-operator`) | `atm msg --to worker --re T-002 "…"` | existing |
| **Answer** | question / stuck item | Same as Message, recipient = asker, about = the item's `re`. Enter sends the answer; Shift+Enter for a new line; IME composition is left alone | `POST /msg` | `atm msg --to worker --re T-002 "…"` | existing |
| **Send** | composer (button or Enter) | — | `POST /msg` | `atm msg …` | existing |
| **Set as lead** | recipient picker | — | `POST /api/v1/lead {seat}` (existing) | — | existing |
| **Keep** / **Remove** | escalated automated node | Confirm | `POST /api/v1/ticket/T-009/decide {keep: true/false}` | `atm capture`/`atm discard` per the node's `automated_action` | build ticket confirms which CLI verb; out of the mockup |

### 6.4 Start an agent (the one action the app does not run)

`Start an agent…` opens a sheet that says which seat, the harness the board recorded for it, and the exact command: `atm spawn worker --persist`, with Copy. Copy reads `Runs a harness on your machine with your credentials, so the app does not start it for you. Paste this in a terminal.` This is a deliberate decision: the read/write API never launches processes today, and a spawn carries the operator's auth. If the operator wants the app to spawn, that is a separate ticket with its own gate.

### 6.5 What the dialogs never do

No dialog offers "accept latest", "accept anyway", or an accept without notes. Reject needs a reason. Assign never picks a seat by itself. The composer never prefills a ticket that is not on this board.

### 6.6 Setup actions for the empty states

`Set objective…` opens a sheet with the objective and done-when fields and calls `POST /api/v1/objective` (`atm objective --set "…" --exit "…"`), only when the objective is empty. `Create ticket…` calls `POST /api/v1/ticket` (`atm create …`). Both are in scope for the build only if the operator wants them; the mockup shows them as buttons and states that.

## 7. States

Each state is in the mockup's state switcher and in `docs/design/app-v3/` at 1440 and 390 (index in `docs/design/app-v3/README.md`, which also lists the Details, panel, dialog, filter, result and autocomplete captures).

| State | What is true | What the screen shows |
|---|---|---|
| **empty** | No tickets, no objective | Objective: `No objective yet…` with `Set objective…`; strip: `0 working · 0 needs you · 0 blocked · 0 done`; one line: `This board has no tickets…` with `Create ticket…`; Talk to Everyone |
| **fresh** (quickstart) | 3 sample tickets, nobody running, no lead | `0 working · 0 needs you · 0 blocked · 0 done`; Needs you: `Nothing needs you right now.`; Working: empty copy with `Assign…` (primary) then `Start an agent…` (quiet); Up next: T-001 `Nobody has it. Ready to start.` with `Assign…`, T-002 `Waits on T-001.`, T-003 `Waits on T-002.`; Talk to Everyone with `Start an agent…` |
| **working** | worker running T-001 for 12 min | `1 working · 0 needs you · 0 blocked · 0 done`; Working: T-001 `worker has it, running for 12 min.`; Talk to worker `running T-001 for 12 min` |
| **needs-you** | T-001 in review at `3f2a1c0…`; worker asked a question about it | `0 working · 2 needs you · 0 blocked · 0 done`; Up next: T-002 `Waits on T-001, which is in review.`; Needs you: T-001 review card with Accept/Reject, and the question with Answer; Accept dialog shows the full sha; filters `All (2) · Mentions (1) · Accept (1) · Blocked (0)` |
| **blocked** | T-002 blocked "needs staging DB credentials"; worker idle | `0 working · 1 needs you · 1 blocked · 1 done`; Blocked: T-002 `Blocked by worker 40 min ago: "…"` with Answer and Reassign…; Needs you also lists the block because it names the operator |
| **accepted** | T-001 accepted by boss; T-002 working | Objective progress `1 of 3 accepted`; Done: T-001 `Accepted by boss 2 h ago on 3f2a1c0.` with proof line; Working: T-002 |
| **offline** | worker limited until 17:40, T-002 claimed by it | Needs you: `worker is limited until 17:40. T-002 is waiting on it.` with Start… and Reassign…; Blocked: T-002 `worker is limited until 17:40…` with Reassign… leading; Talk opens with Everyone (nothing is running); picking worker shows `limited until 17:40` and the queued-message note |
| **stale** | T-001 was drawn at `3f2a1c0` but worker pushed `9c1d2e3` after the screen loaded | Accept dialog: no primary; `Changed since you opened this: new head 9c1d2e3` and `Review again`; `Review again` redraws with the new head and notes (§6.2) |
| **after accept** (what the mockup shows when you complete the Accept dialog) | boss accepted T-001 | The card's buttons become `Accepted · by boss · 3f2a1c0`; on the next board read T-001 is in Done (§6.1) |
| **self-review** (variant shown in the Accept dialog) | operator is the author | Accept disabled with the reason beside it: `You wrote this, so you can't accept it.` |
| **read-only** (variant of top bar) | `atm ui` without `--operator` | `Read-only. Start with atm ui --operator <name> to act.`; all buttons disabled with that title |

## 8. Visual system

- **Type:** system UI stack, 14 px body / 1.45, titles 15 px / 600, objective 20 px / 600, section headers 12 px / 600 uppercase tracked, muted 13 px. Monospace only for ids, shas and commands.
- **Colour:** paper background `#f7f7f5`, card white, ink `#1a1a1a`, muted `#6b6b6b`, line `#e4e4e0`. One accent for primary buttons `#2f5bea`. Status: working `#1f8a4c`, needs-you `#b7791f`, blocked `#c53030`, done `#6b6b6b`. Colour appears as a 3 px left rail on cards and on the count pills, not as text colour, so the page stays calm. Dark theme via `prefers-color-scheme`, same tokens.
- **Buttons:** real `<button>`s, 32 px tall, 8 px radius, primary filled, secondary outlined, danger outlined red. Never a link styled as a button and never a command box where a button belongs. Result lines (§6.1) are 13 px muted with the proof in monospace; disabled reasons are 13 px in the needs-you amber.
- **Presence dots:** 9 px. Solid for working and limited, a 1.5 px ring for idle and logged out, ring with a slash for host offline, dotted ring for unknown. Colour and shape both differ, so the states survive greyscale.
- **Chips** (Needs-you filters): 24 px tall, 999 px radius, selected = ink on paper inverted. **Badges** (ticket mentions in chat): 20 px tall, monospace id, a 6 px dot in the phase colour, phase word and owner in muted text.
- **Panel handle:** 6 px wide hit area on the panel's left edge, `col-resize` cursor, 1 px line that turns to the accent while dragging.
- **Density:** 24 px between sections, 8 px between cards, 16 px card padding. Max 880 px main column so lines stay readable at 1440.
- **Motion:** none beyond the panel sliding in.

## 9. Decisions recorded for the build (T-1486)

1. **Four new write routes** (`accept`, `reject`, `assign`, `msg` stays the existing one) wrap the CLI functions; no second gate. `POST /msg` is reused as is.
2. **Spawn stays a copy command** (§6.4).
3. **The lead is never auto-picked** (§4). `board.app.lead` from `POST /api/v1/lead` is honoured; otherwise the single running seat; otherwise Everyone.
4. **Needs-you count = questions + stuck + escalated + reviews waiting on the operator + stopped/limited seats with claimed work.** This is the only count computed client-side, and it is a union of API items, not an estimate. The build should move it server-side into `needs-you` so the strip reads one field.
5. **Sections replace views.** `#T-002` in the URL opens the panel; no other routing.
6. **Keep and Remove for escalated nodes** need the build to confirm the verbs; the mockup shows the buttons.
7. **Setup actions** (`Set objective…`, `Create ticket…`) are optional for the build; the empty states must still render their copy without them.
8. **Presence comes from liveness records, not from guesses** (§5.8). `working` and `idle` are `agents[].state`; `host offline` is `reachable == false` with the API's `adapter_reason`; `unknown` is a seat with no `seen_h` and no adapter state. The doing-now words are the ticket's last note, never generated.
9. **The thread route grows a `re` filter** (`GET /api/v1/thread?re=T-001`) for the panel, read-only, same shape as `?with=`.
10. **Stale-view check is a re-read before the write** (§6.2), client-side; the server gate is unchanged and still refuses a wrong sha.
11. **Result lines echo the API response** (§6.1). The build must render what `accept` returned (`by`, `sha`), not the dialog's inputs.
12. **Drafts, panel width and the Done fold live in the browser** (`localStorage`); nothing about the operator's view is written to the board.

## 10. What is out

Graph/columns plan layouts, Fleet and Runs as separate views, evals, rooms, search, pins, voice. All were in the v2 spec; none answer the four questions and all can return later behind Details or as a separate screen.

**Looked at in revision 2 and not adopted** (from the study of another human+agent workspace):

- **Chat-first with no dependencies.** The board's dependency graph is what makes `atm next` hand out the right ticket; the screen stays board-first, with chat beside it and threads per ticket (§5.9).
- **Letting anyone mark done.** Done still means accepted by a second seat at an exact head (§1). A seat's own "done" shows as `done, not accepted` and never counts.
- **Free-form "emergent" roles.** Seats keep their registered roles from `atm join`; the presence strip shows what a seat is doing, not a role it picked for itself.
