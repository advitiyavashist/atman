# App v3 mockup screenshots (T-1485)

Taken from `ui/mockups/app-v3.html` with headless Chromium at 1440×900 and 390×844 (light scheme, full page). Reproduce with `?state=<name>` plus `&details=1`, `&open=T-001`, `&dialog=accept|reject|assign|start`, `&tab=talk` on the mockup file. Data is the quickstart board (project `demo`, operator `boss`, seat `worker`, tickets T-001..T-003); see `docs/design/app-v3.md` §7 for what each state asserts.

| State | 1440 | 390 | Shows |
|---|---|---|---|
| Empty board | `1440-empty.png` | `390-empty.png` | No objective, no tickets: `Set objective…`, `Create ticket…`, four zero counts, Talk to everyone |
| Fresh quickstart | `1440-fresh.png` | `390-fresh.png` | Three samples in Up next, nobody running, `Start an agent…`, no lead picked |
| Working | `1440-working.png` | `390-working.png` (Talk tab) | worker running T-001 for 12 min; Talk opens with the one running agent |
| Needs you | `1440-needs-you.png` | `390-needs-you.png` | T-001 waiting for accept with the sha on the button; worker's question with Answer; count `2 needs you` |
| Accept dialog | `1440-needs-you-accept.png` | `390-needs-you-accept.png` | Full 40-char review head, the seat's notes, required accept notes |
| Blocked | `1440-blocked.png` | `390-blocked.png` | T-002 blocked in worker's words, Answer + Reassign; T-001 accepted with proof line |
| Accepted | `1440-accepted.png` | `390-accepted.png` | `1 of 3 accepted`; Done card `Accepted by boss 2 h ago on 3f2a1c0` |
| Agent offline | `1440-offline.png` | `390-offline.png` | worker limited until 17:40; needs-you item with Reassign; queued message |
| You are the author | `1440-self-review-accept.png` | `390-self-review-accept.png` | Accept disabled: "You submitted this, so you cannot accept it." |
| Read-only | `1440-read-only.png` | `390-read-only.png` | `atm ui` without `--operator`: banner, every button disabled |
| Details on | `1440-details.png` | `390-details.png` | Phases, evidence, transcript path, usage with age, receipts, diff command |
| Ticket sheet | `1440-sheet.png` | `390-sheet.png` | Drill-down: body, review head, verdicts, acceptance proof, messages about it |
| Assign dialog | `1440-assign.png` | `390-assign.png` | Seat list with each seat's real state; no seat preselected, Assign refuses until one is picked |
