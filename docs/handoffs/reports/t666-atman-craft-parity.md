# T-666 — Atman UI craft-parity audit vs Steer console

**Status:** audit + ticket proposals only. No rewrite in this run.  
**Auditor tip:** `origin/main` `cc8cb2b9a2eb21e6d0f03274b72e48823296e638` (2026-09-09, merge of PR #63).  
**Operator console (what `tickets ui` serves):** `tickets.py` `UI_HTML`.  
**Parallel console (Vite, not what `tickets ui` prints):** `ui/`.  
**Steer checkout:** not available on this Cloud Agent VM. `git clone` / `git ls-remote` of `advitiyavashist/steer` returned `Repository not found` (private or token-scoped). Environment repos = Atman only. Steer comparison below is against the T-666 brief (Try→Review→Evidence→Activate density, brand mark, empty, nav, tokens) plus Atman’s own Steer lock in `docs/brand/atman-tokens.md`.

Craft bar (both products): easy onboard, fewest turns / least cost visible, slick + practical. Not kitsch, not a confusing console, not a memory-brain.

---

## Surfaces checked

| Surface | Path | SHA-256 prefix (file bytes at tip) |
|---|---|---|
| Embedded console | `tickets.py` `UI_HTML` (~L9599–10435) | `51f2fe897835` |
| React shell / IA | `ui/src/App.tsx`, `ui/src/components/NavBar.tsx` | `5cd13e84e632`, `b318d8c9369c` |
| Day-one / empty | `ui/src/components/DayOnePath.tsx`, `EmptyStateView.tsx`, `ui/src/screens/Overview.tsx` | `9d46cf4003b0`, `b7c1b00d0b75`, `2aea177e36a3` |
| Tokens / mark | `ui/src/styles.css`, `ui/src/components/FormationMark.tsx` | `54f20261e87e`, `284839111f77` |
| Server empty copy | `src/ticket_board/server/views.py` `EMPTY_STATES` | `3e184cb2ee4f` |
| Brand / TEAM IA | `docs/brand/atman-tokens.md`, `docs/brand/atman-brand-direction-v1.md`, `docs/product/atman-team-ia-v1.md` | `2c7362280a9d`, `e9d717a42c93`, `e543720f22e9` |
| Landing (aligned tokens) | `landing/index.html`, `landing/styles.css` | `b690ce986280`, `711f241d680f` |
| Stale Ticket Board spec | `docs/interface-v1.md`, `docs/prototype.html`, `ui/README.md` | `4d1716caef4e`, `2dd93aacd3f8`, `a8643e329ff5` |
| Guards | `tests/test_t571_ux_bar.py`, `tests/test_t606_family_identity.py`, `tests/ui/overview.test.tsx` | `f004b8436cb4`, (family identity), `147ef0c7b089` |

Prior craft work already on tip: T-571 UX bar (PR #57), T-575 docs (PR #56), T-606 family identity, Team seats no-pitch (PR #48).

---

## Checklist (1–8)

### 1. Empty states / day-one onboard

**tickets.py (`emptyBoard`)** — T-571 3-step is present and copy-locked: Work → Team → Objective, CLI CTAs, honesty line, Intervene always available (`renderEmptyBoard`, ~L10359). That is the right *card*.

**Day-one is not a single surface.** On an empty board the Work pane still also renders:

- persistent Next strip (`quickstart`)
- Objective promise strip
- 7-step Onboarding `<details>` (open by default)
- promise hero (`—` / `—`)
- four kanban columns each saying `none`
- Turns efficiency `<details>` forced `open` while both hero values are unknown (`renderPromise` ~L10019)

Steer’s empty (per brief) is one lifecycle path (Try). Atman’s empty is a stack. The 3-step card is good; the stack around it is the craft gap.

**React Overview** — `DayOnePath` matches the same 3-step copy (`ui/src/copy.ts`). Listing screens still use generic `EmptyStateView`.

### 2. Onboarding checklist completeness

Complete vs T-571: `first_merge` → `tickets merge`; `second_harness` → `tickets join <name> --harness …`. Seven probes in `_onboarding_checklist` (~L10438).

**Soft nit (called in the brief):** display order is initialized → first ticket → first agent → first review → first merge → **second harness** → objective. emptyBoard and TEAM IA demo path put second harness as step 2 and objective as step 3, *before* review/merge. Checklist teaches the long loop; emptyBoard teaches day one. They disagree.

### 3. Nav IA + density

| | `tickets ui` | React `ui/` | Steer (brief) |
|---|---|---|---|
| Primary nav | Objective · Team · Work · Intervene | Overview · Tickets · Agents · Messages · Activity | Try → Review → Evidence → Activate |
| Default | Work | Overview | first lifecycle step |
| Chrome | brand, portfolio, master/CoS/done chips, promise chips, sprint bar, pulse, live, clock, refresh, theme | brand, board tag, theme, Connect | quiet mark + stage nav |

`tickets ui` IA matches `docs/product/atman-team-ia-v1.md`. React IA still matches retired `docs/interface-v1.md` (“Ticket Board”, five items). Two consoles is the largest craft miss: Steer has one density; Atman has two products.

Header on `tickets ui` is the clutter: sprint “no active sprint” + 0-width bar, clock, last-updated, portfolio of disconnected products, and promise chips *plus* a second promise strip *plus* a hero. Quiet chrome would keep lockup + 4 tabs + one promise pair + live.

### 4. Brand lockup / mark

**Pass on `tickets ui`:** lowercase `atman` + 5 formation-dots, lime `--acc:#c8f04a`, no `↗ Ticket Board`, no sports leftovers (guarded by `tests/test_t571_ux_bar.py` `BANNED`). Landing uses the same lockup and tokens.

**Allowed Steer `^`:** portfolio caret is the literal `^` (`test_atman_mark_and_portfolio_caret_have_separate_jobs`). `docs/brand/atman-tokens.md` says the caret belongs to steer.md and may appear only on portfolio nav. **T-665 (Steer 45° chevron) is noted, not blocking.**

**Fail on React + leftovers:** `ui/src/styles.css` still ships retired blue `--blue:#5b8def` / `#0c0e12` (tokens.md migration table). `docs/prototype.html` still titles “Ticket Board” with `↗`. Brand docs still list `self ↔ whole` as *Use* language while UI tests ban that string from chrome.

### 5. Promise / hero metrics honesty + visibility

**`tickets ui` honesty is good.** Unknown is `—`, never `$0` (`_promise_hero`, `fmtMedian`/`fmtYield`, yield hint). Chips stay in the header on every tab (T-571). Done (24h) is labeled as 24h, not lifetime, not harness turns.

**Visibility is split and noisy.** Hero lives on **Work**, not Objective. Chips + strip + hero repeat the same two numbers. React has *no* live median/yield surfaces; `done24hLabel` exists only in `copy.ts` + a unit test — Agents never renders it.

### 6. Dark tokens / a11y

| Check | `tickets ui` | React `ui/` |
|---|---|---|
| Dark tokens vs `atman-tokens.md` | aligned | retired blue / charcoal |
| `focus-visible` | lime 2px (`--acc`) | blue 3px (`--blue`) |
| `type="button"` | tabs / Msg / refresh / theme yes; **`#cSend` Post has no `type`** | mixed; App header, Overview refresh, EmptyStateView, several Agents/Tickets controls omit it |
| `prefers-reduced-motion` | yes | no |
| Contrast (`--mute` `#8fa4a6` on `--bg` `#0b1416`) | ~7:1, AA/AAA small text | `--muted` `#8a8d96` on `#0c0e12`, similar |

No P0 contrast bug on the live console. React token drift is the a11y/brand issue.

### 7. Dual empty (DayOnePath + EmptyStateView) — T-586

**Overview dual-empty is closed on tip.** `Overview.tsx` renders `DayOnePath` only when empty; `tests/ui/overview.test.tsx` asserts the server `empty_state.headline` is *not* shown.

Residual (not T-586 reopen):

- Server `EMPTY_STATES["overview"]` is still “No work yet. Create a ticket or import a board.” / “New ticket” — dead copy that contradicts day-one.
- Tickets / Agents / Messages / Activity still use `EmptyStateView` (correct for *filtered* lists; Agents “Connect your first agent” is a different onboard than emptyBoard step 2).
- `tickets ui` stacked empty (item 1) is a separate, live gap.

### 8. Kitsch / anti-brain / self↔whole

**Live `UI_HTML` is clean.** Banned sports, mandala, `self ↔ whole`, `shared-memory brain`, `Steer ^` string, pitch classes — asserted in `test_t571_ux_bar.py`. Team ledes are the lock: “Who’s present. What’s uncovered.” / “Open seat — uncovered work.”

`self ↔ whole` remains in brand + TEAM IA docs (design footnote). `brain` appears only in comments / knowledge docs (“not a shared-memory brain”). No UI essay. No action.

---

## Gap table

| Gap | Severity | Surface | Rec ticket title | Notes |
|---|---|---|---|---|
| Two consoles, two IAs (`tickets ui` TEAM vs React Ticket Board) | **P0** | `tickets.py` vs `ui/` + `docs/interface-v1.md` | T-667: Declare one operator console; park or retarget React | `tickets ui` is what README / first-session send people to. React still 5-item Overview IA, blue tokens, no promise chips. Do **not** rewrite React in the NOW slice — decide + hide/label. |
| Day-one stack: emptyBoard + empty kanban + open turns + 7-step onboard + Next + hero | **P1** | `tickets.py` `pane-board` | T-668: Single empty on Work (hide kanban/hero/turns while `empty_board`) | Closest Steer-empty miss. 3-step card stays; collapse the rest. Auto-open turns-when-unknown fights empty. |
| Header chrome density (sprint/clock/portfolio/pulse always on) | **P1** | `tickets.py` `header.cmd` | T-668b: Quiet chrome — lockup + tabs + one promise pair + live | Steer-like quiet. Fold sprint when none; clock/last-updated behind live; portfolio optional. |
| Promise hero not on Objective; triple-repeated metrics | **P1** | `tickets.py` Objective vs Work | T-670: One promise pair above the fold; hero belongs with Objective or chips only | Honesty already correct. Density is the bug. |
| React tokens still retired blue; no Done (24h) on seats | **P1** | `ui/src/styles.css`, `Agents.tsx` | child of T-667, or T-671 if React stays | Only if React remains an operator surface. Landing + `tickets ui` already lime. |
| Day-one CTAs are CLI-only (no in-console Try) | **P1** | `emptyBoard` / `DayOnePath` | T-672: In-console first actions (or copy-to-run), not three dead commands | Steer Try is a click. Atman prints `tickets quickstart`. Bigger than a one-liner; not a rewrite of the board. |
| Onboarding checklist order vs emptyBoard / demo path | **P2** | `renderOnboarding` | T-673: Put Second harness + Objective before review/merge | Soft nit from the brief. Completeness already OK. |
| `#cSend` and several React buttons omit `type="button"` | **P2** | `tickets.py` L9942; `ui/src/App.tsx` etc. | T-674: `type="button"` sweep | Tiny. `#cSend` is a one-liner if someone is already in `UI_HTML`. |
| Server overview `empty_state` copy contradicts DayOnePath | **P2** | `src/ticket_board/server/views.py` | T-675: Align overview `empty_state` with day-one 3-step | T-586 UI is done; leftover server copy can resurrect a dual-empty if Overview regresses. |
| Stale Ticket Board docs / prototype `↗` | **P2** (docs) | `docs/interface-v1.md`, `docs/prototype.html`, `ui/README.md` | **handoff → T-669 PM** | Do not write docs PRs here. |
| Brand docs still *Use* `self ↔ whole` while UI bans it | **P2** (docs) | `docs/brand/atman-brand-direction-v1.md` | **handoff → T-669 PM** | Keep as mark footnote or drop from “Use”. |
| Steer `^` / 45° chevron | note | portfolio caret | **T-665** (Steer brand) | Out of scope. Atman caret is allowed on portfolio only. Do not block T-666 children. |

---

## NOW (this week) vs later

### NOW — ship this week (small, operator-visible)

1. **T-668 — Single empty on Work.** If `empty_board`, show only the 3-step card (+ Next). Hide kanban, promise hero, and do not force-open Turns. Highest craft delta, localized to `renderEmptyBoard` / `renderPromise` / pane markup.
2. **T-668b — Quiet header.** Hide sprint row when no sprint; drop or fold clock; keep formation-dots + `atman` + 4 tabs + median/yield chips + live. No IA change.
3. **T-667 decision (not a rewrite):** one sentence in TEAM IA / README — *operator console = `tickets ui`*. React remains the HTTP/SSE contract client until a dedicated port. Stops the split from being treated as two equal products.
4. **T-674 one-liner (optional ride-along):** `type="button"` on `#cSend`.

### Later

- T-670 promise-density (move or drop hero; Objective owns the standing mission + one metric pair).
- T-672 in-console first actions (needs write API honesty — don’t fake a click).
- T-673 checklist reorder.
- T-671 / React token+IA port — only after T-667 says React is still an operator surface. Otherwise leave it as the contract client.
- T-675 server empty_state copy.
- T-669 PM docs (below). T-665 Steer chevron: Steer-side, do not block.

---

## Proposed child tickets (implement later)

Copy-paste ready. Do not implement in this PR.

### T-667 — One operator console (`tickets ui`); park React as contract client

- **Why:** Steer has one craft surface. Atman tip has TEAM IA in `UI_HTML` and Ticket Board IA in `ui/`.
- **Done when:** README / TEAM IA name `tickets ui` as the operator console; React README states it is the HTTP/SSE client, not the craft bar. No React rewrite required.
- **Out:** porting React to Objective/Team/Work/Intervene (separate, larger).

### T-668 — Single empty + quiet chrome on `tickets ui`

- **Why:** empty Work is a stack; header is a status wall. Fights “slick + practical”.
- **Done when:** `empty_board` ⇒ only Day-one 3-step + Next; kanban/hero/turns hidden. No sprint chrome when `sprint` is null. Promise chips remain. Tests in `test_t571_ux_bar.py` updated, bans unchanged.
- **Out:** in-console Try (T-672); checklist reorder (T-673).

### T-670 — One promise pair

- **Why:** chips + strip + hero repeat; hero sits on Work.
- **Done when:** operator sees median turns + yield@cost once above the fold; `—` still means unknown. Turns table stays behind a closed `<details>` until there is a number.

### T-672 — Day-one actions that can be taken

- **Why:** Steer Try is in-product. Atman prints three CLI lines.
- **Done when:** each emptyBoard step is a copyable control or a real write with an honesty strip if the UI cannot write. No fake success.

### T-673 — Checklist order follows day one

- Display: Board ready → You registered → Second harness → Objective set → Work claimed → First review → First merge.
- Probes unchanged.

### T-674 — `type="button"` sweep

- `tickets.py` `#cSend`; React buttons listed in §6.

### T-675 — Overview `empty_state` matches DayOnePath

- Change `EMPTY_STATES["overview"]` headline/detail/action to the 3-step / quickstart language, or stop sending `empty_state` when Overview uses DayOnePath.

---

## Handoffs to T-669 (PM / docs) — do not write docs PRs here

- `docs/interface-v1.md` and `ui/README.md` still specify Ticket Board / five-nav / blue taste. They contradict TEAM IA and `tickets ui`.
- `docs/prototype.html` still ships `↗ Ticket Board`.
- Brand “Use: self ↔ whole” vs UI ban — pick one.
- Server overview empty copy vs DayOnePath (or land with T-675).
- Document T-667 decision: which URL is the console in first-session / master-howto (they already say `tickets ui`; React `npm run dev -w ui` is the undocumented twin).

---

## Out of scope (as ordered)

- **T-665** Steer 45° chevron — note only. Atman portfolio `^` stays legal per tokens.md.
- **T-669** docs/IA completeness — listed as handoffs, not edited.
- No React rewrite, no token port, no emptyBoard redesign beyond the proposed tickets.

---

## What is already at the craft bar (do not re-litigate)

- Formation-dots + lowercase `atman` on `tickets ui` and landing.
- TEAM IA four tabs + seat Msg / Intervene.
- emptyBoard 3-step copy + honesty (`—` ≠ 0).
- Done (24h) on the embedded roster.
- Sports / pitch / mandala / brain-essay bans on `UI_HTML`.
- T-586 Overview dual-empty (DayOnePath vs EmptyStateView) — **verified gone on tip**.
- Lime action token + focus-visible on the embedded console.
