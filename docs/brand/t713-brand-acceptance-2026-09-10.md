# T-713 brand acceptance — Atman (2026-09-10)

**Audience:** `cursor-atman-ui-v3`, landing, PMM. **Steer DLP copy stays on Steer.**  
**Sources:** T-722 `steer:docs/research/DISCOURSE_HN_REDDIT_X_2026-09-10.md`; T-715 pain→parity→spike lock; user correction on this ticket (any first seat; turns/cost secondary).

This packet is the Atman voice lock. Do not invent a second homepage story.

---

## Decision

| | Lock |
|---|---|
| **Spine** | **Team runtime** for agents you already bring. |
| **Qualifier** | **BYOA** = bring-your-own-agent *harness*, not “install our model.” |
| **Rejected primary** | **Agent swarms.** HN treats “100 agents” as hype; Anthropic’s own docs prefer a single session / subagents for sequential / same-file work. “Swarm” is SECONDARY hype language (e.g. Addy Osmani), not our noun. |
| **First seat** | **Any.** The product must not require Claude Code. Example order in copy: **Claude Code, Codex, Cursor, Grok Bot, custom.** |
| **Primary spike** | **Clone → sit a first seat → `tickets ui`.** |
| **Secondary outcomes** | **Fewest turns** and **least measured cost** (median turns / yield@cost). Honesty: `—` until a done ticket reports. Unknown is not zero. Not the H1. |
| **Vs Steer** | Steer = advisory **DLP / Try the API** / `POST /v1/evaluate`. Atman never borrows that CTA, light-instrument chevron chrome, or policy-enforcement language. |

Say: *Bring the agents you already use. We make them one team.*  
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

Hero = team runtime / BYOA. Turns and cost live in the promise strip, not the H1.  
No `swarm`. No `Try the API`. No `evaluate` CTA. No Presidio / DLP / auditor-log Steer bleed.  
Formation-dot mark, lowercase `atman`, dark board `#0b1416`. MIT. Usage-limit recovery before the start block.

---

## UI guidance (`cursor-atman-ui-v3`)

- Keep TEAM IA: Objective · Team · Work · Intervene. Day-one is sit a seat + `tickets ui`, not Steer’s Try→Review→Evidence→Activate.
- Do not restyle Atman as Steer: no caret-as-logo, no light lime instrument as the page, no “Try the API,” no advisory Block/DLP chrome.
- Empty / day-one copy: first seat is **any of the five**. Claude Code may be the illustrated example, never the only supported seat.
- Turns and cost stay secondary honesty (`—` until measured). Do not promote them to the product name.
- Review this packet against the UI PR when it lands; reject Steer resemblance.

---

## Steer (restated, not reopened)

Steer #140 / T-711: CTA **Try the API**; spike = one `POST /v1/evaluate` on `model_output`; pain = agent output leaks; parity vs Presidio / cloud DLP; **advisory only**. No swarm bleed onto Steer. Chevron size is a separate landing ticket.
