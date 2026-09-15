# T-992 author evidence: launch repair on current main

**App-only successor:** replay T-810/T-884/T-992 onto `origin/main` `457767b` without PR156 ancestry (no train-2 backend heads, no T-931/T-932/T-956, no knowledge/session_boundary).

**Code SHA:** `1252e362bb10ab0c699d7a4f8e9c6af60e6f7db2` (branch `fix/t992-app-only`, PR #203; parent app-only `68a054f`). Evidence below was regenerated from this checkout after `run_t992.py` started deriving `ROOT` from `Path(__file__).resolve().parents[3]` so the documented repro executes this successor, not a prior seat tree.
**Board:** throwaway `/tmp/atman-t992-verify/repo/.tickets`, seeded exactly like T-986 (six tickets: working, waiting, hold, reserved, REVIEW without verdict, done-in-chat). Never the live board.
**Driver:** `run_t992.py` (this directory), headless Chrome via Playwright, `tickets.py ui` from this SHA.
**Focused tests:** 118 passed (`tests/test_t992_launch_repair.py` plus t791 / t889 / t810_* / t884 / t944 / t323 / t1010 / t1004 / t1005 / t981), `/usr/bin/python3` 3.9.6, `TICKET_SEAT` unset.

## The three required changes

1. **Done without ACCEPT is visible and named.** `work_view.unverified_done_ids` feeds `workflow_graph(keep_done=)`, so T-006 (status `done`, note "I completed it in chat", no `review_events`) stays on the Work graph and list. Its node reads `DONE · UNVERIFIED` with the exact copy **Marked done; verification not recorded**; the deep link `?work=T-006` opens the detail with the same review line and `ACCEPTANCE none recorded`. The legend reads `Done 1 · 1 unverified`. The header chip is `0/6 accepted` with a separate amber `1 done, unverified` chip; a done flag never increments the accepted count. Accepted done work (structured `atm accept` on the exact artifact, or a `tickets merge` note) leaves the active view as before (`test_active_graph_omits_unrelated_done`, now seeded with an ACCEPT). A chat-style "ACCEPT" note or message stays unverified (T-944).
2. **Transport label.** A successful board refresh renders `Board synced` (was `connected`). `reconnecting` / `offline` are unchanged; before the first snapshot the pill reads `syncing`. Connected / authenticated / responding remain agent states on Team with their own evidence.
3. **390x844 first screen.** On ≤600px the brand and transport share one row; master / CoS / accepted / unverified chips, median turns, yield@cost, sprint bar and the health pulse fold into one `Status` details whose summary carries `0/6 accepted · 1 unverified` (closed on phones, always open on desktop, one tap to unfold: shot 06). The standing-objective strip hides on the Work tab only, where the Work objective card already shows objective + Done when (it stays on the Objective tab: check 07). The next-step action strip and its unreachable state are untouched. Measured at 390x844: Objective card 345–474px, Finishing from 498px, Blocked from 570px, Next step from 663px; no horizontal overflow; no page errors.

## Preserved (re-checked on the same throwaway board)

T-005 REVIEW stays `Submitted … Awaiting review of <sha> · no verdict recorded`, never Accepted (check 02). Light mode `--acc #6b5344` (check 04). Mutation pending/confirmed/failed, offline≠lost, reload resync and the same-second reopen boundary are untouched code paths (focused suite covering them passed at this SHA).

## Shots

- `01-desktop-1280-work-dark.png` header chips + Board synced + legend + T-006 on graph
- `02-desktop-1280-T-006-detail.png` deep link detail, review line
- `03-desktop-1280-list-T-006.png` list row
- `04-desktop-1280-light.png` light theme
- `05-mobile-390x844-first-viewport.png` **viewport-only** first screen (not full page)
- `05b-mobile-390-full.png` full page for context
- `06-mobile-390-status-unfolded.png` folded status opened
- `08-mobile-390-list-T-006.png` phone list row

## Checks (`results.json`, 22/22)

| check | result | detail |
|---|---|---|
| `01-transport-label` | PASS | connStatus='Board synced' |
| `01-header-count` | PASS | chips=master bossCoS —0/6 accepted1 done, unverified |
| `01-desktop-status-open` | PASS | hdrStatus.open=True (no fold on desktop) |
| `01-legend-unverified` | PASS | legend=Ready Reserved 1 Task posted Working 1 In review 1 Blocked Waiting 1 Capture Hold 1 Done 1 · 1 unverified |
| `01-graph-shows-T-006` | PASS | T-006 node count=1 text=T-006Done · unverifiedFinished without review@bobMarked done; verification not recorded |
| `02-T-005-submitted-not-accepted` | PASS | T-005 detail=Close / T-005 / IN REVIEW / backend / P2 / Needs a verdict / STATUS / Submitted by @alice · alice@e92bd75 / WHO / @alice / ACCEPTANCE / none recorded / ARTIFACT / alice@e92bd75 / REVIEW / Awaiting review of e92bd75 · no verdict recorded / LAST NOTE / alice REVIEW: alice@e92bd75 -- paths: tickets.py; |
| `02-deeplink-T-006-detail` | PASS | T-006 detail=Close / T-006 / DONE / docs / P2 / Finished without review / STATUS / Marked done 3.3h ago by @bob / WHO / @bob / ACCEPTANCE / none recorded / ARTIFACT / — / REVIEW / Marked done; verification not recorded / LAST NOTE / bob I completed it in chat / tickets show T-006 / tickets msg --to bob --re T-00 |
| `03-list-shows-T-006` | PASS | list rows=1 |
| `04-light-acc` | PASS | light --acc=#6b5344 |
| `05-mobile-status-folded` | PASS | hdrStatus.open=False |
| `05-mobile-status-brief` | PASS | brief='0/6 accepted · 1 unverified' |
| `05-mobile-objective-strip-folded-on-work` | PASS | promiseStrip={'top': 0, 'bottom': 0, 'h': 0, 'visible': False} |
| `05-mobile-objective-in-first-viewport` | PASS | workObjective={'top': 345, 'bottom': 474, 'h': 129, 'visible': True} |
| `05-mobile-finishing-starts-in-first-viewport` | PASS | Finishing={'top': 498, 'bottom': 560, 'h': 62, 'visible': True} |
| `05-mobile-blocked-starts-in-first-viewport` | PASS | Blocked={'top': 570, 'bottom': 653, 'h': 83, 'visible': True} |
| `05-mobile-next-starts-in-first-viewport` | PASS | Next step={'top': 663, 'bottom': 745, 'h': 83, 'visible': True} |
| `05-mobile-no-horizontal-overflow` | PASS | scrollWidth=390 |
| `05-mobile-transport-label` | PASS | connStatus='Board synced' |
| `06-mobile-fold-reachable` | PASS | chips=master bossCoS —0/6 accepted1 done, unverified pulse=Warning · 6 |
| `07-mobile-objective-tab-keeps-strip` | PASS | promiseStrip on Objective tab={'top': 266, 'bottom': 418, 'h': 152, 'visible': True} |
| `08-mobile-list-shows-T-006` | PASS | rows=1 |
| `09-no-page-errors` | PASS | pageerrors=[] |

Repro (throwaway only): `env -u TICKET_SEAT -u TICKETS_DIR -u TICKET_AGENT /usr/bin/python3 docs/reviews/t992/run_t992.py`
