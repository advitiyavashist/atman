# Atman brand direction v1

Status: **locked for V1.** SVG assets live under `docs/brand/assets/`.

Atman **is** a runtime for teams of agents you already bring. One-liner:

> Bring the agents you already use. We make them one team.

| | |
|---|---|
| **IS** | Runtime for teams of agents you already bring. Feels like TEAM: objective → allocate → concurrent → handoffs → review → replan. North star = task completion in fewest turns. BYOA = bring-your-own-agent *harness*, not install our model. |
| **NOT** | Multi-agent framework. Shared-memory product. One API for Claude/Codex. Model router alone. DAG toy. |
| **Language** | teammates, lanes, next step, handoff — not nodes / edges / SDK. |
| **Don't** | Promise learned router / shadow optimizer as V1 brand. No compliance / shield / enforce chrome. Don't collapse Atman into tickets-CLI marketing — product is the team runtime; CLI is how it runs today. |

Total Football: fluid roles, coverage over fixed identity. Visual: dark command board `#0c0e12`, paper/ink contrast on chrome.

---

## 1. Name

**Atman** as the product name.

| Surface | Form |
|---|---|
| UI chrome / wordmark | `atman` lowercase |
| Prose, tickets, docs | Atman (title case) |

Domain purchase is out of scope for this doc.

---

## 2. Mark — formation dots (Week-1)

3–5 dots in a loose football pitch / constellation. At 16–32px it must read as a **team formation**, not a flowchart. Fluid spacing. Works on dark `#0c0e12`.

**Rejected:** locks/shields, node–edge DAG icons, robot anthropomorphs, neon glow spam, spiritual kitsch.

Assets: [assets/mark.svg](assets/mark.svg), [assets/lockup.svg](assets/lockup.svg).

---

## 3. Palette / tokens

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0c0e12` | Command board / pitch |
| `--fg` | `#e8e6e1` | Primary text |
| `--mute` | `#8a8d96` | Meta, labels |
| `--line` | `#22262e` | Rules, card edges |
| `--card` | `#141820` | Seats, panes |
| `--acc` | `#5b8def` | Default action / ready |
| `--ok` | `#3dbe7a` | Verified success |
| `--warn` / `--intervene` | `#e0a53d` | Attention / Intervene CTAs |
| `--bad` | `#e85d4c` | Failure / blocked |
| `--review` | `#9b7dff` | Review lane |
| `--live` | `#3ee8c5` | Live seat pulse only (sparse) |

Paste-ready rules: [atman-tokens.md](atman-tokens.md).

No fake utilization percentages. Status color is evidence-backed or it is not shown.

---

## 4. Tone rules

**Use:** teammates, seats, lanes, coverage, next step, handoff, intervene, objective, roster, BYOA, fewest turns.

**Ban:** DAG, nodes, edges, orchestration framework, "multi-agent SDK", fake telemetry walls, compliance/shield/enforce chrome, spiritual kitsch.

The board is a pitch. Empty seats are uncovered work. The operator intervenes; the product does not silently auto-promote.

See also: [TEAM IA v1](../product/atman-team-ia-v1.md).
