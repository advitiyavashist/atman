# Atman brand direction v1

Status: **CEO / Advitiya review.** Soft-lock pending ACCEPT. No SVG assets in this PR (T-347 waits ACCEPT). Does not change `tickets.py` `UI_HTML` (T-345 owns UI).

Atman is a **BYOA runtime**. One-liner:

> Bring the agents you already use. We make them one team.

UX is a **TEAM view**, not a DAG. Total Football: fluid roles, coverage over fixed identity. Agent-facing objective: task completion in the **fewest turns**.

Not a multi-agent SDK. Not shared-memory-only. Not model-router-only.

Contrast product: [steer.md](https://steer.md) = "Policy for what your AI says" — light instrument panel, slight-pixel `^`, paper / ink / lime. Atman is a **different product-family member**, not a Steer reskin.

---

## 1. Name

**Recommend (soft-lock): Atman** as the product name.

| Surface | Form |
|---|---|
| UI chrome / wordmark | `atman` lowercase |
| Prose, tickets, docs | Atman (title case) |

**HOLD** hard domain / URL purchase. Same deferral pattern as Steer T-138. Do not invent or recommend a domain in this review.

---

*Etymology (footnote only): Sanskrit ātman ≈ self / soul. Mention in naming notes if asked. Never spiritual kitsch, mandalas, Om, or "awakening" copy in marketing.*

---

## 2. Positioning contrast vs Steer

| | Steer | Atman |
|---|---|---|
| Job-to-be-done | Policy for what your AI **says** | Runtime that makes the agents you already use **one team** |
| Visual temperature | Light paper / ink / lime instrument panel | Dark command / pitch `#0c0e12` |
| Mark language | Slight-pixel `^` (direction, policy) | Team formation (coverage, seats on a pitch) |
| Claims honesty | No fake metrics | No fake metrics, no fake telemetry walls |

Same family, different job. Sharing a lime or a `^` would read as a reskin. Do not do that.

---

## 3. Mark vibe shortlist

### A — Formation dots **(RECOMMEND, Week-1)**

3–5 dots in a loose football pitch / constellation. At 16–32px it must read as a **team formation**, not a flowchart. Fluid spacing. Works on dark `#0c0e12`.

### B — Open ring / continuum A *(alternate)*

Abstract open ring: self ↔ team, no lettermark clutter. Keep it geometric. Avoid Om / mandala vibes.

### C — Linked seats *(fallback only)*

Two overlapping rounded seats / cards: BYOA agents joining one roster. Risk: generic SaaS. Use only if A fails readability at 16px.

**Week-1:** ship A. Hold B as the alternate. Do not spend cycles on C unless A fails.

### Rejected (do not explore)

- Steer `^` reuse
- Locks / shields
- Node–edge DAG icons
- Robot / agent anthropomorphs
- Neon cyberpunk glow spam

SVG assets ship in **T-347** after CEO ACCEPT of this shortlist. Inline SVG / data-URI only when UI (T-345) needs a mark.

---

## 4. Palette / tokens (draft for T-345 / T-347)

Align with T-276 dark command-board roots already in flight. Do not invent a second dark theme.

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0c0e12` | Command board / pitch |
| `--fg` | `#e8e6e1` | Primary text |
| `--mute` | `#8a8d96` | Meta, labels |
| `--line` | `#22262e` | Rules, card edges |
| `--card` | `#141820` | Seats, panes |
| `--acc` | `#5b8def` | Default action / ready |
| `--ok` | `#3dbe7a` | Verified success |
| `--warn` | `#e0a53d` | Attention |
| `--bad` | `#e85d4c` | Failure / blocked |
| `--review` | `#9b7dff` | Review lane (purple) |
| `--chip` | `#1c2433` | Chips (T-276) |

**Proposal (pending CEO ACCEPT) — distinctive Atman accent, not Steer's pale lime:**

| Token | Proposed | Use |
|---|---|---|
| `--live` | `#3ee8c5` electric mint | Intervene / live only. Sparse. Never a page wash, never a glow field. |

`--acc` stays T-276 blue for everyday actions. `--live` is the "someone is on the pitch / operator is intervening" signal. Reject if it reads lime-adjacent to Steer; swap to a warmer signal amber *only if* `--warn` `#e0a53d` is not already carrying that load.

No fake utilization percentages. Status color is evidence-backed or it is not shown.

---

## 5. Tone rules

**Use:** team, seats, lanes, coverage, intervene, objective, roster, BYOA, fewest turns.

**Ban:** DAG, nodes, edges, orchestration framework, "multi-agent SDK", fake telemetry walls, Purview / compliance chrome, spiritual kitsch.

The board is a pitch. Empty seats are uncovered work. The operator intervenes; the product does not silently auto-promote.

---

## 6. Week-1 brand scope

In this PR: wordmark rule + recommended mark direction (A) + token draft.

**T-347** (after ACCEPT): SVG wordmark + formation-dots mark. Inline SVG / data-URI only inside UI. No asset folder until ACCEPT.

**T-345** (ui, after T-276 / T-323): apply tokens and mark in `UI_HTML`. Out of scope here.

**HOLD:** Steer T-138 / T-341 domain and hard-URL decisions stay soft-hold. Same rule for Atman.

See also: [TEAM IA v1](../product/atman-team-ia-v1.md) (T-348).
