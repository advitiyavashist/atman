# Atman TEAM IA v1

Surface is a TEAM view, not a DAG editor.

Atman is a runtime for teams of agents you already bring. One-liner: *Bring the agents you already use. We make them one team.*

| | |
|---|---|
| **IS** | Runtime for teams of agents you already bring. Feels like TEAM: objective → allocate → concurrent → handoffs → review → replan. First seat is any of Claude Code, Codex, Cursor, Grok Bot, custom. BYOA = bring-your-own-agent *harness*, not install our model. |
| **NOT** | Agent swarms. Multi-agent framework. Shared-memory product. One API for Claude/Codex. Model router alone. DAG toy. Claude-only first seat. |
| **Secondary** | Task completion in fewest turns at least measured cost — not the homepage claim. |
| **Language** | teammates, lanes, next step, handoff — not nodes / edges / SDK. |
| **Don't** | Promise a learned router or shadow optimizer as V1 brand. No compliance / shield / enforce chrome. Don't collapse Atman into tickets-CLI marketing — the product is the team runtime; the CLI is how it runs today. |

Atman can sit next to a separate policy product. Keep the jobs distinct: Atman is who works and how the team finishes. Do not mix policy-enforcement claims into Atman.

Roles are fluid: seats cover lanes; identity is not a permanent persona.

Related: [brand direction v1](../brand/atman-brand-direction-v1.md).

**CEO/PM PRIORITY lock (Advitiya agent chats):** a per-agent chat is a seat-scoped thread on the existing `tickets msg` / inbox surface (`messages.jsonl`). The repo-backed knowledge graph is a separate evidence and skill system, not a second chat store. IA stays Objective · Team · Work · Intervene. No pitch chrome.

---

## Primary surfaces (first-class)

### 1. Objective

Standing mission from `board_snapshot` (`goals` / `tickets objective`).

- Editable if a write API exists.
- Else read-only, plus an honesty strip: *read-only — set via `tickets objective`*.
- No invented progress bars. Met / not met / evidence only when the board has it.

### 2. Team

Seats / cards, not a workforce table-as-identity.

Each seat shows: status, quota / limit, utilization (real numbers only), lane / coverage. Intervene **Msg** opens that seat’s agent-scoped thread (same `tickets msg --to` log).

Copy (brand lock): **Who’s present. What’s uncovered.** Empty seat: **Open seat — uncovered work.** Coverage: **Coverage by work, not fixed role.**

Empty seats = **uncovered work**. That is the point of the view. Roles flex; do not pin a seat to a permanent persona. Spare dark seat cards + lanes. Quiet formation-dots as constellation / **self ↔ whole** — never a literal pitch, goals, or grass. No Om / mandala kitsch.

### 3. Work

Kanban: **Blocked | Ready | In flight | Review**.

Language: teammates, lanes, next step, handoff — not nodes / edges / SDK. This is the work surface, not a dependency-graph editor. Deps may appear as waiting-on chips on a card. Operators do not draw nodes or edges.

Keep the four-column board. Do not replace it with a DAG.

### 4. Intervene

Operator actions on a seat or a card:

| Action | Meaning |
|---|---|
| route | send work to a seat / lane |
| unblock | clear a blocker the operator can clear |
| reassign | move a claim |
| nudge | wake / remind a seat |
| msg | talk to a seat (agent-scoped 1:1 thread) or the board |

Confirm destructive actions. **No silent auto-promote.** Where the product is advisory only, say so on the control (honesty strip), do not dress it as a command that fired.

---

## Secondary surfaces

| Surface | Notes |
|---|---|
| Messages | Thread from `board_snapshot.messages` (same `tickets msg` log). Board (everyone) plus per-seat 1:1 threads for BYOA seats. Keep; do not make it the home. Advitiya PRIORITY agent chats. |
| Turns / efficiency | Fewest-turns objective; no fake telemetry wall. |
| Onboarding | Empty / next / checklist. First-run path, not a fifth primary pane. |
| Team seats | A view of Team, not a separate product. Spare dark seats; formation-dots as constellation / presence only. |
| Knowledge | Separate repo-backed evidence/skill graph. Show task-relevant inherited facts and source links; do not dump a memory feed. |

---

## Demo path (≤ 10 min)

1. Quickstart
2. Join 2 harnesses
3. Set objective
4. Watch work move Blocked → Ready → In flight → Review
5. One intervene (route, unblock, reassign, nudge, or msg)
6. One merge

If a step needs a write the UI does not have yet, stop and show the honesty strip. Do not fake the click.
