# Atman brand direction v1

Status: **CEO / Advitiya review.** Soft-lock pending ACCEPT. No SVG assets in this PR (T-347 waits ACCEPT). Does not change `tickets.py` `UI_HTML` (T-345 owns UI).

Atman **is** a runtime for teams of agents you already bring. One-liner:

> Bring the agents you already use. We make them one team.

**PMM lock (keep)**

| | |
|---|---|
| **IS** | Runtime for teams of agents you already bring. Feels like TEAM: objective → allocate → concurrent → handoffs → review → replan. North star = task completion in fewest turns. BYOA = bring-your-own-agent *harness*, not install our model. |
| **NOT** | Multi-agent framework. Shared-memory product. One API for Claude/Codex. Model router alone. DAG toy. |
| **Contrast** | Atman = who works / how the team finishes. Steer = what the agent may see/say. |
| **Language** | teammates, lanes, next step, handoff — not nodes / edges / SDK. |
| **Steer (keep separate)** | "Runtime policy checks for AI agents." Advisory allow / block / escalate + evidence. DLP = GTM wedge. Cite only GO-WITH-DEBT / ACCEPT-with-limits — never mix into Atman claims. Do not rebrand Steer. |
| **Don't** | Promise learned router / shadow optimizer as V1 brand (T-313 / T-315 = NICE). No compliance / shield / enforce chrome. Don't collapse Atman into tickets-CLI marketing — product is the team runtime; CLI is how it runs today. |

Steer **Path B decided** (CEO): product name SteerMD, domain `steermd.com`. PMM's "HOLD T-138 / don't lock domain" is **superseded** — T-138 is not an open HOLD; Advitiya registration residual only. Do not reopen A/B/C.

Steer launch is **P0, parallel** with Atman (dual-track). Soft-hold Steer landing hero **T-341** only for this Atman direction turn — not "Steer waits forever." Visual: light instrument panel, slight-pixel `^`, paper / ink / lime. Atman is a **different product-family member**, not a Steer reskin. Total Football: fluid roles, coverage over fixed identity.

---

## 1. Name

**Recommend (soft-lock): Atman** as the product name.

| Surface | Form |
|---|---|
| UI chrome / wordmark | `atman` lowercase |
| Prose, tickets, docs | Atman (title case) |

**HOLD** Atman hard domain / URL purchase in this review (do not invent a domain). Steer domain is already decided — see §2. Do not write HOLD T-138.

---

*Etymology (footnote only): Sanskrit ātman ≈ self / soul. Mention in naming notes if asked. Never spiritual kitsch, mandalas, Om, or "awakening" copy in marketing.*

---

## 2. Positioning contrast vs Steer

| | SteerMD (`steermd.com`, Path B decided) | Atman |
|---|---|---|
| Job-to-be-done | What the agent may **see/say**. Launch hero: "Runtime policy checks for AI agents." | Who **works** / how the team **finishes**. BYOA runtime: one team, fewest turns. |
| Visual temperature | Light paper / ink / lime instrument panel | Dark command / pitch `#0c0e12` |
| Mark language | Slight-pixel `^` (direction, policy) | Team formation (coverage, seats on a pitch) |
| Claims honesty | Advisory allow/block/escalate + evidence. DLP = GTM wedge. Cite GO-WITH-DEBT / ACCEPT-with-limits only. No fake metrics. | No fake metrics, no fake telemetry walls. Never mix Steer policy claims into Atman. |
| Track | P0 launch, **parallel** with Atman. T-341 hero paused *this turn only*. | This direction turn. Does not stall Steer. |

Same family, different job. Sharing a lime or a `^` would read as a reskin. Do not do that. Do not reopen Steer A/B/C.

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

**Use:** teammates, seats, lanes, coverage, next step, handoff, intervene, objective, roster, BYOA, fewest turns.

**Ban:** DAG, nodes, edges, orchestration framework, "multi-agent SDK", fake telemetry walls, Purview / compliance / shield / enforce chrome, spiritual kitsch.

The board is a pitch. Empty seats are uncovered work. The operator intervenes; the product does not silently auto-promote.

---

## 6. Week-1 brand scope

In this PR: wordmark rule + recommended mark direction (A) + token draft.

**T-347** (after ACCEPT): SVG wordmark + formation-dots mark. Inline SVG / data-URI only inside UI. No asset folder until ACCEPT.

**T-345** (ui, after T-276 / T-323): apply tokens and mark in `UI_HTML`. Out of scope here.

**Steer (do not reopen):** Path B decided — product name SteerMD, domain `steermd.com`. T-138 is not an open HOLD; Advitiya registration residual only. Dual-track: Steer launch stays P0 parallel with Atman. Soft-hold T-341 landing hero **this Atman direction turn only**.

**Atman domain:** HOLD purchase in this review. No invented URL.

See also: [TEAM IA v1](../product/atman-team-ia-v1.md) (T-348).
