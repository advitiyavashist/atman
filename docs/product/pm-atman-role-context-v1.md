# Atman V1 — role context framework (LOCK)

**Status:** Advitiya lock 2026-09-08 (PM draft → CEO/CTO brief)  
**Owner:** PM  
**Epic:** E-013 (role context framework)  
**Source:** adapted from Steer `claude-review@448e4e2` (T-531); implementation verified on Atman `origin/main`.

## One-liner

Ship a **framework first**: when Atman spins up a seat, it **knows the role**, **injects that context**, and **can update it** over time. Users will customize backends later; BYO memory stays allowed.

## In V1 (MUST — framework)

| Capability | Behavior |
|---|---|
| Role-aware seat | Join/spawn records lane/role; prompt knows which role(s) this seat covers |
| Role context store | Standing context keyed by **role** (plus existing per-agent brief) — file-based default under `.tickets/briefs/` |
| Inject on wake | Every `watch`/`spawn` prompt file includes: role context(s) + agent brief + ticket context notes |
| Update path | `tickets brief --role` (or UI) can append or replace **role** and **agent** context; updates show on next wake |
| Shared lane brief | Optional `_shared.md` + `roles/<role>.md` so multiple seats on the same lane get the same baseline |
| Honesty | If role context is empty, say so — don't invent lore |

## Optional / customize later (NOT a closed platform)

- Users may plug their own memory backend (vector DB, graph docs, company wiki) behind the same inject contract.
- Graph-based docs are **welcome depth** of the framework, not a forced product surface on day one.
- Not: mandatory shared-memory "brain," multi-agent SDK, or "we replace your memory stack."

## Non-goals (still out)

- Learned router / shadow scheduler as brand promise
- Forcing one memory vendor or API
- Collapsing Atman into a knowledge-base product

## Acceptance (framework slice)

1. After `join … --roles backend`, next wake prompt contains **role=backend** context block (or explicit empty).
2. Updating role brief is visible on the **next** wake without re-join.
3. Custom harness still gets the **same** prompt file contract (BYOA unchanged).
4. Docs say: framework default; swap/extend backend as you grow.

Operator path: [onboarding/role-context.md](../onboarding/role-context.md).

## Build order (starter tickets)

1. Spec + contract (this doc → eng brief)
2. `roles/<role>.md` + inject into `prompt_text` / prompt file
3. CLI/UI update path for role briefs
4. Quickstart/BYOA docs: role context on spin-up
5. Optional adapter stub for BYO memory (NICE-thin)

## Supersedes

Prior "Atman is not a shared-memory product / graph docs NICE later" as a hard V1 exclusion. Replaced by: **framework-first role context; customize backends; graph docs optional depth.**
