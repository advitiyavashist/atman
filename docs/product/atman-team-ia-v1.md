# Atman TEAM IA v1

Status: **lock for V1.** Surface is a TEAM view, not a DAG editor. UI implementation is T-345 (`UI_HTML`); this doc does not change that file.

Atman **is** a BYOA runtime (bring-your-own-agent *harness*, not "install our model"). One-liner: *Bring the agents you already use. We make them one team.*

Feels like TEAM: **objective → allocate → concurrent → handoffs → review → replan.** Total Football: seats cover lanes; identity is fluid. North star: finish work in the **fewest turns**.

**Not:** multi-agent framework, shared-memory product, one API for Claude/Codex, model router alone, DAG toy. Product is the team runtime; CLI is how it runs today — do not collapse marketing into `tickets`.

**PM contrast (keep):** Atman = **who works / how the team finishes**. SteerMD = **what the agent may see/say** (launch hero: "Runtime policy checks for AI agents"). Steer stays advisory allow/block/escalate + evidence; DLP = GTM wedge; cite GO-WITH-DEBT / ACCEPT-with-limits only — never mix into Atman claims.

**Path B decided** — SteerMD / `steermd.com`. Do not reopen Steer A/B/C. Do not write HOLD T-138 (registration residual only). Dual-track: Steer launch is P0 parallel with Atman. Soft-hold T-341 landing hero **this Atman direction turn only** — not "Steer waits forever."

Related: [brand direction v1](../brand/atman-brand-direction-v1.md) (T-346). Tickets: T-345, T-276, T-323, T-346, T-347, T-348.

---

## Primary surfaces (first-class)

### 1. Objective

Standing mission from `board_snapshot` (`goals` / `tickets objective`).

- Editable if a write API exists.
- Else read-only, plus an honesty strip: *read-only — set via `tickets objective`*.
- No invented progress bars. Met / not met / evidence only when the board has it.

### 2. Team

Seats / cards, not a workforce table-as-identity.

Each seat shows: status, quota / limit, utilization (real numbers only), lane / coverage.

Empty seats = **uncovered work**. That is the point of the view. Roles flex (Total Football); do not pin a seat to a permanent persona.

T-276's agents pane is the seed. Rename and reframe to Team in T-345.

### 3. Work

Kanban: **Blocked | Ready | In flight | Review**.

Language: teammates, lanes, next step, handoff — not nodes / edges / SDK. This is the work surface, not a dependency-graph editor. Deps may appear as waiting-on chips on a card. Operators do not draw nodes or edges.

T-276 already ships this four-column board. Keep it. Do not replace it with a DAG.

### 4. Intervene

Operator actions on a seat or a card:

| Action | Meaning |
|---|---|
| route | send work to a seat / lane |
| unblock | clear a blocker the operator can clear |
| reassign | move a claim |
| nudge | wake / remind a seat |
| msg | talk to a seat or the board |

Confirm destructive actions. **No silent auto-promote.** Where the product is advisory only, say so on the control (honesty strip), do not dress it as a command that fired.

---

## Secondary surfaces

| Surface | Notes |
|---|---|
| Messages | Thread from `board_snapshot.messages`. Keep; do not make it the home. |
| Turns / efficiency | After T-312. Fewest-turns objective; no fake telemetry wall. |
| Onboarding | Empty / next / checklist from T-323. First-run path, not a fifth primary pane. |
| Pitch / formation | If T-276 still has a formation / pitch reading, it is a view of Team, not a separate product. |

---

## Demo path (≤ 10 min)

1. Quickstart
2. Join 2 harnesses
3. Set objective
4. Watch work move Blocked → Ready → In flight → Review
5. One intervene (route, unblock, reassign, nudge, or msg)
6. One merge

If a step needs a write the UI does not have yet, stop and show the honesty strip. Do not fake the click.

---

## Ownership

| Lane | Owns | Tickets |
|---|---|---|
| brand | Direction, mark, tokens | T-346 (this family), T-347 (SVG after CEO ACCEPT) |
| ui | Panes in `UI_HTML` | T-345, after T-276 / T-323 |
| pm | Copy and journey | T-336 |

T-348 is this IA lock. T-345 implements it. T-347 does not start until brand shortlist ACCEPT.

**Steer (do not reopen):** Path B decided — SteerMD / `steermd.com`. T-138 is not an open HOLD (Advitiya registration residual only). T-341 hero copy is soft-held **this turn only**; Steer launch stays P0 parallel. No Atman URL decision in this doc.
