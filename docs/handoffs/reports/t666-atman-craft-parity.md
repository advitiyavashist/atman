# T-666 — Atman UI craft-parity audit vs Steer console

**Status:** audit + implement-ticket map only. No rewrite in this run.  
**Auditor tip (re-audit):** `origin/main` `d80581c` (2026-09-09). UI / `tickets.py` / brand files cited below are byte-identical to `cc8cb2b`; the only tip delta since the first pass is README honesty copy.  
**Operator console (what `tickets ui` serves):** `tickets.py` `UI_HTML`.  
**Parallel console (Vite, not what `tickets ui` prints):** `ui/`.  
**Steer checkout:** not available on this Cloud Agent VM. `git clone` / `git ls-remote` of `advitiyavashist/steer` returned `Repository not found` (private or token-scoped). Environment repos = Atman only. Steer comparison is against the T-666 brief (Try→Review→Evidence→Activate density, brand mark, empty, nav, tokens) plus Atman’s Steer lock in `docs/brand/atman-tokens.md`.

Craft bar (both products): easy onboard, fewest turns / least cost visible, slick + practical. Not kitsch, not a confusing console, not a memory-brain.

**Board SoT for ticket ids.** Rec column uses the live board only. Do not mint suffixes.

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
| Guards | `tests/test_t571_ux_bar.py`, `tests/test_t606_family_identity.py`, `tests/ui/overview.test.tsx`, `tests/ui/copy.test.ts` | `f004b8436cb4`, (family identity), `147ef0c7b089`, copy test |

Prior craft work already on tip: T-571 UX bar (PR #57), T-575 docs (PR #56), T-606 family identity, Team seats no-pitch (PR #48). T-669 PM docs/IA canonical is **DONE**.

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

Steer’s empty (per brief) is one lifecycle path (Try). Atman’s empty is a stack. The 3-step card is good; the stack around it is the craft gap (**T-678**).

**React Overview** — `DayOnePath` matches the same 3-step copy (`ui/src/copy.ts`). Listing screens still use generic `EmptyStateView` (**T-675**). Embedded Team / Intervene empties are a separate tickets-ui gap (**T-674**).

### 2. Onboarding checklist completeness

Complete vs T-571: `first_merge` → `tickets merge`; `second_harness` → `tickets join <name> --harness …`. Seven probes in `_onboarding_checklist` (~L10438).

**Soft nit:** display order is initialized → first ticket → first agent → first review → first merge → **second harness** → objective. emptyBoard and TEAM IA demo path put second harness as step 2 and objective as step 3, *before* review/merge. Checklist teaches the long loop; emptyBoard teaches day one. They disagree. Soft bucket: **T-682**.

### 3. Nav IA + density

| | `tickets ui` | React `ui/` | Steer (brief) |
|---|---|---|---|
| Primary nav | Objective · Team · Work · Intervene | Overview · Tickets · Agents · Messages · Activity | Try → Review → Evidence → Activate |
| Default | Work | Overview | first lifecycle step |
| Chrome | brand, portfolio, master/CoS/done chips, promise chips, sprint bar, pulse, live, clock, refresh, theme | brand, board tag, theme, Connect | quiet mark + stage nav |

`tickets ui` IA matches `docs/product/atman-team-ia-v1.md`. React IA still matches retired `docs/interface-v1.md` (“Ticket Board”, five items). Two consoles is the largest craft miss: Steer has one density; Atman has two products. Lock SoT: **T-673**. React nav density only if React becomes ship: **T-676**.

Header on `tickets ui` is the clutter: sprint “no active sprint” + 0-width bar, clock, last-updated, portfolio of disconnected products, and promise chips *plus* a second promise strip *plus* a hero. Quiet chrome: **T-679**. One promise pair: **T-680**.

### 4. Brand lockup / mark

**Pass on `tickets ui`:** lowercase `atman` + 5 formation-dots, lime `--acc:#c8f04a`, no `↗ Ticket Board`, no sports leftovers (guarded by `tests/test_t571_ux_bar.py` `BANNED`). Landing uses the same lockup and tokens.

**Allowed Steer `^`:** portfolio caret is the literal `^` (`test_atman_mark_and_portfolio_caret_have_separate_jobs`). `docs/brand/atman-tokens.md` says the caret belongs to steer.md and may appear only on portfolio nav. **T-665 logo HOLD only — not blocking.**

**Fail on React + leftovers:** `ui/src/styles.css` still ships retired blue `--blue:#5b8def` / `#0c0e12` (tokens.md migration table). `docs/prototype.html` still titles “Ticket Board” with `↗`. Brand docs still list `self ↔ whole` as *Use* language while UI tests ban that string from chrome. Token/IA leftovers ride **T-673** (SoT) and, if React ships, **T-676**.

### 5. Promise / hero metrics honesty + visibility

**`tickets ui` honesty is good.** Unknown is `—`, never `$0` (`_promise_hero`, `fmtMedian`/`fmtYield`, yield hint). Chips stay in the header on every tab (T-571). The embedded roster renders **Done (24h)** as 24h completions, not lifetime, not harness turns (`tickets.py` ~L10324).

**React Done (24h) — precise remainder (T-571 / PR #57 already landed the label):** `ui/src/copy.ts` exports `done24hLabel = "Done (24h)"` and `tests/ui/copy.test.ts` asserts that constant. `ui/src/screens/Agents.tsx` does **not** import or render `done24hLabel` (or any “Done (24h)” string). Remaining React gap is **rendering** on Agents (plus tokens/IA), not label absence.

**Visibility is split and noisy on `tickets ui`.** Hero lives on **Work**, not Objective. Chips + strip + hero repeat the same two numbers (**T-680**). React has no live median/yield surfaces.

### 6. Dark tokens / a11y

| Check | `tickets ui` | React `ui/` |
|---|---|---|
| Dark tokens vs `atman-tokens.md` | aligned | retired blue / charcoal |
| `focus-visible` | lime 2px (`--acc`) | blue 3px (`--blue`) |
| `type="button"` | tabs / Msg / refresh / theme yes; **`#cSend` Post has no `type`** | mixed; App header, Overview refresh, EmptyStateView, several Agents/Tickets controls omit it |
| `prefers-reduced-motion` | yes | no |
| Contrast (`--mute` `#8fa4a6` on `--bg` `#0b1416`) | ~7:1, AA/AAA small text | `--muted` `#8a8d96` on `#0c0e12`, similar |

No P0 contrast bug on the live console. React token drift is the a11y/brand issue (**T-673** / **T-676** if ship). `type="button"` + overview `empty_state` align: **T-682**.

### 7. Dual empty (DayOnePath + EmptyStateView) — T-586

**Overview dual-empty is closed on tip.** `Overview.tsx` renders `DayOnePath` only when empty; `tests/ui/overview.test.tsx` asserts the server `empty_state.headline` is *not* shown.

Residual (not T-586 reopen):

- Server `EMPTY_STATES["overview"]` is still “No work yet. Create a ticket or import a board.” / “New ticket” — dead copy that contradicts day-one (**T-682**).
- Tickets / Agents / Messages / Activity still use `EmptyStateView` (**T-675**). Agents “Connect your first agent” is a different onboard than emptyBoard step 2.
- `tickets ui` Work stack is **T-678**. Team / Intervene empty craft is **T-674**.

### 8. Kitsch / anti-brain / self↔whole

**Live `UI_HTML` is clean.** Banned sports, mandala, `self ↔ whole`, `shared-memory brain`, `Steer ^` string, pitch classes — asserted in `test_t571_ux_bar.py`. Team ledes are the lock: “Who’s present. What’s uncovered.” / “Open seat — uncovered work.”

`self ↔ whole` remains in brand + TEAM IA docs (design footnote). `brain` appears only in comments / knowledge docs (“not a shared-memory brain”). No UI essay. No action. T-669 (docs/IA canonical) is **DONE**; leftover brand-footnote wording is not a new implement ticket.

---

## Gap table

| Gap | Severity | Surface | Rec ticket | Notes |
|---|---|---|---|---|
| Two consoles, two IAs (`tickets ui` TEAM vs React Ticket Board) | **P0** | `tickets.py` vs `ui/` + `docs/interface-v1.md` | **T-673** — Atman UI: lock craft SoT — tickets ui UI_HTML vs React ui/ | `tickets ui` is what README / first-session send people to. React still 5-item Overview IA, retired blue tokens, no promise chips. Decide SoT; do not rewrite React in the NOW slice. |
| Day-one stack: emptyBoard + empty kanban + open turns + 7-step onboard + Next + hero | **P1** | `tickets.py` `pane-board` | **T-678** — Atman tickets ui: single empty on Work (collapse day-one stack) | Closest Steer-empty miss. 3-step card stays; collapse the rest. Auto-open turns-when-unknown fights empty. |
| Header chrome density (sprint/clock/portfolio/pulse always on) | **P1** | `tickets.py` `header.cmd` | **T-679** — Atman tickets ui: quiet header chrome (fold sprint/clock/portfolio noise) | Steer-like quiet. Fold sprint when none; clock/last-updated behind live; portfolio optional. |
| Promise hero not on Objective; triple-repeated metrics | **P1** | `tickets.py` Objective vs Work | **T-680** — Atman tickets ui: one promise pair (drop chips+strip+hero triple) | Honesty already correct. Density is the bug. |
| React listing empties / EmptyStateView copy vs Steer honesty | **P1** | `EmptyStateView.tsx`, listing screens, `views.py` EMPTY_STATES | **T-675** — Atman React: EmptyStateView + listing empty copy craft (Steer honesty) | Overview DayOnePath is done (T-586). Tickets/Agents/Messages/Activity still generic cards. |
| `tickets ui` Team / Intervene empty vs Steer bar | **P1** | `tickets.py` Team + Intervene panes | **T-674** — Atman tickets ui: secondary empty craft (agents/messages) to Steer bar | Work empty is T-678. This is the other panes. |
| Day-one CTAs are CLI-only (no in-console Try) | **P1** | `emptyBoard` / `DayOnePath` | **T-681** — Atman day-one: in-console first actions (not CLI-only CTAs) | Steer Try is a click. Atman prints `tickets quickstart`. Honesty strip if the UI cannot write. |
| React nav still Overview/Tickets/Agents/Messages/Activity | **P2** (if React ships) | `ui/src/components/NavBar.tsx` | **T-676** — Atman React: nav density (groups/symbols/workspace) if React becomes ship | Only after T-673 says React is a ship surface. Retired blue tokens ride here or with T-673. |
| Onboard order + `#cSend`/`type="button"` + overview `empty_state` leftover | **P2** | `renderOnboarding`, `#cSend`, `views.py` | **T-682** — Atman UI soft: onboard order + type=button + overview empty_state align | Completeness already OK. Soft nits only. |
| Steer `^` / 45° chevron | HOLD | portfolio caret | **T-665** — logo HOLD only | Not blocking. Atman caret is allowed on portfolio only. |

T-669 PM docs/IA canonical: **DONE** (not recommended). No other ids.

---

## NOW vs later

Implement order (ui-owned): **T-673 → T-678 / T-679 NOW → T-674 / T-680 / T-681 → soft T-682 / React T-675 / T-676**.

### NOW

1. **T-673** — Lock craft SoT: operator console = `tickets ui` `UI_HTML`. React is the HTTP/SSE contract client until T-673 says otherwise.
2. **T-678** — Single empty on Work. If `empty_board`, show only the 3-step card (+ Next). Hide kanban, promise hero, and do not force-open Turns.
3. **T-679** — Quiet header. Hide sprint row when no sprint; fold clock; keep formation-dots + `atman` + 4 tabs + one promise pair + live.

### Later

- **T-674** — Team / Intervene empty craft on `tickets ui`.
- **T-680** — One promise pair (drop the chips+strip+hero triple).
- **T-681** — In-console first actions (not CLI-only).
- **T-682** — Soft: onboard order, `type="button"`, overview `empty_state` align.
- **T-675** — React EmptyStateView + listing empty copy.
- **T-676** — React nav density, only if React becomes ship.

---

## Live board map (do not file new ids)

| ID | Title |
|---|---|
| T-673 | Atman UI: lock craft SoT — tickets ui UI_HTML vs React ui/ |
| T-674 | Atman tickets ui: secondary empty craft (agents/messages) to Steer bar |
| T-675 | Atman React: EmptyStateView + listing empty copy craft (Steer honesty) |
| T-676 | Atman React: nav density (groups/symbols/workspace) if React becomes ship |
| T-678 | Atman tickets ui: single empty on Work (collapse day-one stack) |
| T-679 | Atman tickets ui: quiet header chrome (fold sprint/clock/portfolio noise) |
| T-680 | Atman tickets ui: one promise pair (drop chips+strip+hero triple) |
| T-681 | Atman day-one: in-console first actions (not CLI-only CTAs) |
| T-682 | Atman UI soft: onboard order + type=button + overview empty_state align |
| T-669 | PM docs/IA canonical — **DONE** |
| T-665 | logo HOLD only |

---

## Out of scope (as ordered)

- **T-665** logo HOLD — not blocking. Atman portfolio `^` stays legal per tokens.md.
- **T-669** docs/IA — DONE; leftover prototype / brand-footnote lines are not a new ticket.
- No UI rewrite in this PR. No React token port. No emptyBoard redesign beyond the tickets above.

---

## What is already at the craft bar (do not re-litigate)

- Formation-dots + lowercase `atman` on `tickets ui` and landing.
- TEAM IA four tabs + seat Msg / Intervene.
- emptyBoard 3-step copy + honesty (`—` ≠ 0).
- **Done (24h)** rendered on the embedded `tickets ui` roster. React: T-571 / PR #57 added `done24hLabel` and a copy-unit test; Agents still does not render that label.
- Sports / pitch / mandala / brain-essay bans on `UI_HTML`.
- T-586 Overview dual-empty (DayOnePath vs EmptyStateView) — **verified gone on tip**.
- Lime action token + focus-visible on the embedded console.
