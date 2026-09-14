# T-810 — integrated app after T-889 @4346558 / PR 127 @8308f5c

Throwaway board (`/tmp/atman-t810-qa2`, never the live board). Playwright
against system Chrome + `tickets ui`. CEO-adopted T-892 items 1 and 5 in the
shell; items 2–4 from the folded Work module.

| File | State shown |
|---|---|
| `t810-first-screen-light.png` | Objective + Done when above Finishing / named HOLD blocker + waiting count / next Ready · unassigned. Graph shows working vs waiting. |
| `t810-compose.png` | `atman:work-select` filled Intervene `re` with T-001. Channel honesty: named channels planned; inbox read ≠ ACK. |
| `t810-team.png` | Unique runtime ids, stable roles, reachability (alice unreachable — no invented live seat). Metrics stay —. |
| `t810-work-390.png` | 390px Work graph: working / waiting / hold visible. |
| `t810-work-390-detail.png` | 390px selected detail + jump to compose. |

Layout controls are Graph/List/Columns `aria-pressed` toggles. Shell `#workDoneWhen` is marked `[data-done-when]` so the module does not duplicate the criterion.

## After the origin/main 7795cef sync (9224be8, PR #156)

Throwaway board served by this tree's `tickets ui`; Playwright over system
Chrome (`channel="chrome"`), true 390×844 and 1280×1100 viewports. Measured,
not eyeballed: at 390px `scrollWidth == innerWidth == 390` (no page overflow;
only the graph canvas scrolls), light `--acc` resolves to `#6b5344`, and
`#workObjective` (top 276) sits above `#nowStrip` (410) above `#workflowGraph` (694).

| File | State shown |
|---|---|
| `t810-merged-first-screen-light.png` | 1280px light: Objective + Done when, Finishing T-001 @alice, Blocked T-003 HOLD · 1 waiting on deps · 2 blockers, Next T-004 Ready · unassigned, then the graph. |
| `t810-merged-390-detail.png` | 390px `?work=T-003` deep link, after "Open T-003 detail": the HOLD detail is in view (initial `atman:work-select` emitted, jump links visible). |
| `t810-merged-390-compose.png` | 390px after "Compose about T-003": Intervene tab with `re` = T-003 and no recipient auto-filled (HOLD ticket has no assigned teammate). |

Keyboard: Enter on a focused node presses it and fills `re`; Escape releases it with focus kept on the node; the List toggle keeps `aria-pressed` and focus across a poll cycle. Headless Chrome's 500px minimum window width makes `--window-size=390` captures look clipped; that is a capture artifact, hence Playwright here.
