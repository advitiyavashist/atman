# T-713 brand acceptance — Atman (2026-09-10)

**Audience:** `cursor-atman-ui-v3`, landing, PMM. **Steer DLP copy stays on Steer.**  
**Sources:** T-722 `steer:docs/research/DISCOURSE_HN_REDDIT_X_2026-09-10.md`; T-715 pain→parity→spike lock; user correction on this ticket (any first seat; turns/cost secondary).

This packet is the Atman voice lock. Do not invent a second homepage story.

---

## Decision

| | Lock |
|---|---|
| **Category (say this first)** | **A local control plane / team runtime for multiple coding agents in one repo.** |
| **Spine** | **Team runtime** for agents you already bring. |
| **Qualifier** | **BYOA** = bring-your-own-agent *harness*, not “install our model.” |
| **Rejected primary** | **Agent swarms.** HN treats “100 agents” as hype; Anthropic’s own docs prefer a single session / subagents for sequential / same-file work. “Swarm” is SECONDARY hype language (e.g. Addy Osmani), not our noun. |
| **First seat** | **Any.** The product must not require Claude Code. Example order in copy: **Claude Code, Codex, Cursor, Grok Bot, custom.** |
| **Primary spike** | **Clone → sit a first seat → `tickets ui`.** |
| **Secondary outcomes** | **Fewest turns** and **least measured cost** (median turns / yield@cost). Honesty: `—` until a done ticket reports. Unknown is not zero. Not the H1. |
| **Vs Steer** | Steer = advisory **DLP / Try the API** / `POST /v1/evaluate`, lime `#c8f04a` on teal `#0b1416`, caret mark, graph-paper optional. Atman must not share that visual family. |

Say: *A local control plane for coding agents in one repo.* Then: *Bring the agents you already use. We make them one team.*  
Don’t say: agent swarms, 7× tokens as Anthropic gospel (T-722: Verdent/SECONDARY only), “we enforce,” fake utilization, sports/mandala kitsch.

---

## Pain → parity → one spike (Atman)

**Persona:** Someone drowning in parallel agent windows — not someone shopping a CrewAI tutorial.

| Beat | Copy lock | Discourse |
|------|-----------|-----------|
| **Pain** | 3–7 agent windows, pipeline-full context switch, human as permanent unblocker, verification slower than generation. Sequential work often gets worse with more agents. | DIRECT HN 47467922 / 48839984. INDEXED Google 180-config paper via Reddit (−70% sequential; 17.2× error amp). |
| **Parity** | CrewAI / AutoGen / LangGraph = coordination tax and loops. Claude Code agent teams = real but experimental and higher tokens; docs prefer single session / subagents for sequential work. | DIRECT Anthropic agent-teams. INDEXED r/AI_Agents production loops. |
| **One spike** | Clone the runtime. Sit **any** first seat. Open **`tickets ui`.** Cap the story at a small team, not 100 agents. | User correction supersedes T-714 Claude-only spike. |

**Say:** Teams when the graph is wide; otherwise one agent, fewer turns.  
**Don’t say:** Swarm as the product; framework bake-off as the homepage.

---

## Seat examples (order is mandatory)

When listing providers, use this order and these names:

1. Claude Code  
2. Codex  
3. Cursor  
4. Grok Bot  
5. custom  

Grok Bot is a **remote / custom harness** seat, not a fourth local CLI clone. Landing and UI may show one concrete hook in the terminal (`tickets hooks claude` *or* `codex` / `cursor` / `remote`) as long as the surrounding copy says the first seat is interchangeable.

---

## Landing acceptance (this PR)

Hero names the category in one line: **local control plane for coding agents in one repo.** Turns and cost live in the promise strip, not the H1.  
No `swarm` as product. No `Try the API`. No `evaluate` CTA. No Presidio / DLP Steer bleed.  
**Visual (retract T-606 family lime on Atman):** dark `#0c0e12`, bone `#ece8e1`, brass CTA `#c4b49a`. **No** Steer lime `#c8f04a`, **no** teal `#0b1416`, **no** graph-paper grid, **no** caret. Formation-dot mark, lowercase `atman`. MIT. Usage-limit recovery before the start block.

---

## UI visual lock (`cursor-atman-ui-v3`) — do this, not a lime restyle

T-606 family lime on `#0b1416` made Atman look like Steer. **Stop using it on Atman surfaces.**

| | Atman | Steer (do not copy) |
|---|---|---|
| Background | `#0c0e12` | `#0b1416` teal-black |
| Type | bone `#ece8e1` | mint-white `#e6eeea` |
| Action | brass `#c4b49a` | lime `#c8f04a` |
| Live/ok | muted teal `#6f9e96` for status text only | mint wash / lime wash |
| Mark | formation dots | caret `^` |
| Page texture | flat dark, no grid | instrument / optional grid |

- Keep TEAM IA: Objective · Team · Work · Intervene. Day-one is sit a seat + `tickets ui`, not Steer’s Try→Review→Evidence→Activate.
- Empty / day-one copy: first seat is **any of the five**. Claude Code may be the illustrated example, never the only supported seat.
- Turns and cost stay secondary honesty (`—` until measured).
- Update `tickets ui` tokens in `tickets.py` / React to this palette; T-571/T-606 lime asserts are stale vs this lock — replace them, do not keep lime to stay green.

---

## Steer (restated, not reopened)

Steer #140 / T-711: CTA **Try the API**; spike = one `POST /v1/evaluate` on `model_output`; pain = agent output leaks; parity vs Presidio / cloud DLP; **advisory only**. No swarm bleed onto Steer. Chevron size is a separate landing ticket.
