# Atman brand direction v1

Atman is a runtime for teams of agents you already bring.

> Bring the agents you already use. We make them one team.

| | |
|---|---|
| **IS** | Runtime for teams of agents you already bring. Feels like TEAM: objective → allocate → concurrent → handoffs → review → replan. North star = task completion in fewest turns. BYOA = bring-your-own-agent *harness*, not install our model. |
| **NOT** | Multi-agent framework. Shared-memory product. One API for Claude/Codex. Model router alone. DAG toy. |
| **Language** | teammates, lanes, next step, handoff — not nodes / edges / SDK. |
| **Don't** | Promise a learned router or shadow optimizer as V1 brand. No compliance / shield / enforce chrome. Don't collapse Atman into tickets-CLI marketing — the product is the team runtime; the CLI is how it runs today. |

Atman can sit next to a separate policy product (runtime checks for what an
agent may see or say). Keep those jobs distinct: Atman is who works and how
the team finishes. Do not mix policy-enforcement claims into Atman.

---

## 1. Name

**Atman** is the product name.

| Surface | Form |
|---|---|
| UI chrome / wordmark | `atman` lowercase |
| Prose and docs | Atman (title case) |

Do not invent a product URL in this file.

*Etymology (footnote only): Sanskrit ātman ≈ self / soul. Mention in naming notes if asked. Never spiritual kitsch, mandalas, Om, or "awakening" copy in marketing.*

---

## 2. Mark vibe

### A — Formation dots (recommended)

3–5 dots in a loose football pitch / constellation. At 16–32px it must read as a **team formation**, not a flowchart. Fluid spacing. Works on dark `#0c0e12`.

### B — Open ring / continuum A (alternate)

Abstract open ring: self ↔ team, no lettermark clutter. Keep it geometric. Avoid Om / mandala vibes.

### C — Linked seats (fallback only)

Two overlapping rounded seats / cards: BYOA agents joining one roster. Risk: generic SaaS. Use only if A fails readability at 16px.

### Rejected

- Caret / chevron reuse from a sibling policy product
- Locks / shields
- Node–edge DAG icons
- Robot / agent anthropomorphs
- Neon cyberpunk glow spam

Shipped assets live in [`assets/`](assets/).

---

## 3. Palette / tokens

See [atman-tokens.md](atman-tokens.md). Dark command-board roots. Status color is evidence-backed or it is not shown. No fake utilization percentages.

`--live` is a sparse "someone is on the pitch" signal, not a page wash. Intervene controls stay amber (`--warn` / `--intervene`).

---

## 4. Tone rules

**Use:** teammates, seats, lanes, coverage, next step, handoff, intervene, objective, roster, BYOA, fewest turns.

**Ban:** DAG, nodes, edges, orchestration framework, "multi-agent SDK", fake telemetry walls, Purview / compliance / shield / enforce chrome, spiritual kitsch.

The board is a pitch. Empty seats are uncovered work. The operator intervenes; the product does not silently auto-promote.

See also: [TEAM IA v1](../product/atman-team-ia-v1.md).
