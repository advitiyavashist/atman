# App v3 mockup screenshots (T-1485, revision 2)

Taken from `ui/mockups/app-v3.html` with headless Chromium at 1440×900 and 390×844 (light scheme; full page, except panels and dialogs, which are fixed and captured as the viewport). Reproduce with `?state=<name>` plus `&details=1`, `&open=T-001`, `&dialog=accept|reject|assign|start`, `&tab=talk`, `&filter=all|mentions|accept|blocked`, `&result=accept:T-001`, `&done=1`, `&ac=@` on the mockup file. Data is the quickstart board (project `demo`, operator `boss`, seat `worker`; a second seat `astra` joined as reviewer in the later states; tickets T-001..T-003); see `docs/design/app-v3.md` §7 for what each state asserts.

Every shot carries the presence strip under the four counts (§5.8) and Done collapsed unless noted (§5.4).

| State | 1440 | 390 | Shows |
|---|---|---|---|
| Empty board | `1440-empty.png` | `390-empty.png` | No objective, no tickets: `Set objective…`, `Create ticket…`, four zero counts, worker host offline in the presence strip, Talk to everyone |
| Fresh quickstart | `1440-fresh.png` | `390-fresh.png` | Three samples in Up next, nobody running, `Assign…` then `Start an agent…`, no lead picked |
| Working | `1440-working.png` | `390-working.png` (Talk tab) | worker running T-001; presence says what it is doing now from its last note; Talk opens with the one running agent |
| Needs you | `1440-needs-you.png` | `390-needs-you.png` | Filters `All 2 · Mentions 1 · Accept 1 · Blocked 0`; T-001 waiting for accept with the sha on the button; worker's question with Answer; ticket badges (`T-001 · in review · worker`) wherever chat mentions a ticket |
| Needs you, Accept filter | `1440-needs-you-filter.png` | `390-needs-you-filter.png` | Only the review card; the strip count stays `2 needs you` |
| Accept dialog | `1440-needs-you-accept.png` | `390-needs-you-accept.png` | Full 40-char review head, the seat's notes, required accept notes |
| After Accept | `1440-after-accept.png` | `390-after-accept.png` | The button row is now the result line `Accepted · by boss · 3f2a1c0` (§6.1); the next board read moves the card to Done |
| Changed under you | `1440-stale-accept.png` | `390-stale-accept.png` | worker pushed `9c1d2e3` after the screen loaded: no Accept button; `Changed since you opened this: new head 9c1d2e3` and `Review again` (§6.2) |
| Blocked | `1440-blocked.png` | `390-blocked.png` | T-002 blocked in worker's words, Answer + Reassign; astra `host offline` with the API's reason on hover; T-001 in the collapsed Done |
| Accepted | `1440-accepted.png` | `390-accepted.png` | `1 of 3 accepted`; worker `writing tests for the /stats endpoint for T-002`; Done collapsed with its count |
| Done expanded | `1440-done-expanded.png` | `390-done-expanded.png` | Done opened by its header: `Accepted by boss 2 h ago on 3f2a1c0` with the proof line |
| Agent offline | `1440-offline.png` | `390-offline.png` | worker limited until 17:40 (amber), astra `unknown` (dotted ring); needs-you item with Reassign; queued message |
| You are the author | `1440-self-review-accept.png` | `390-self-review-accept.png` | Accept disabled with the reason beside it: "You wrote this, so you can't accept it." |
| Read-only | `1440-read-only.png` | `390-read-only.png` | `atm ui` without `--operator`: banner, every button disabled with the reason on the card |
| Details on | `1440-details.png` | `390-details.png` | Phases, evidence, transcript path, usage with age, receipts, diff command, seat reasons |
| Ticket panel | `1440-panel.png` | `390-panel.png` | Click a card: that ticket's thread with its own composer, then body, review head, verdicts, proof; resizable edge at 1440, full-screen sheet with `‹ Back` at 390 (§5.9) |
| Assign dialog | `1440-assign.png` | `390-assign.png` | Seat list with each seat's presence dot and state; no seat preselected, Assign says `Pick a seat first` until one is picked |
| Autocomplete | `1440-autocomplete.png` | `390-autocomplete.png` | `@` in the composer lists everyone plus each seat with its state; `#` lists tickets with status and owner; Enter picks and never sends (§5.6) |
