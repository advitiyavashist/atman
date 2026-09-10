# Role context on spin-up (know → inject → update)

**Epic:** E-013 (role context framework)  
**Audience:** operators and seats bringing their own agents  
**Spec:** [pm-atman-role-context-v1](../product/pm-atman-role-context-v1.md)  
**Harness entry:** [../byoa.md](../byoa.md)  
**Source:** adapted from Steer `claude-review@448e4e2` (T-531); Atman paths and CLI verified on `origin/main`.

When a seat wakes, Atman should already know its role, inject that standing context, and accept updates without a re-join. That is a **file-first framework**, not a shared-memory product.

Product promise (home UI, not CLI-only): **finish more work at least cost — in the fewest turns.** Role files stay short so the first turn is work, not reading.

## 1. Know the role

`tickets join` / `spawn` records lanes on the seat:

```sh
tickets join $TICKET_AGENT --roles backend
```

Comma-separated roles are allowed (`verification,acceptance`). Each named role maps to `.tickets/briefs/roles/<role>.md`. If the seat has no roles yet, the prompt says so.

Human-readable templates for this repo live under [`roles/`](../../roles/) (for example [`roles/_shared.md`](../../roles/_shared.md)). Copy ideas from them into the board store; inject does **not** read repo-root `roles/` automatically.

## 2. Inject

On `watch` / `spawn` / first wake, the prompt file includes:

| Block | Source | If missing |
|---|---|---|
| Shared | `.tickets/briefs/_shared.md` | Explicit **empty** |
| Role | `.tickets/briefs/roles/<role>.md` | Explicit **empty** |
| Agent | `.tickets/briefs/<agent>.md` | Omit or say none |
| Ticket | Claimed ticket + dependency notes | Printed by `tickets next` / `show` |

Do not invent lore for an empty block. Do not dump `tickets inbox` history into the role block.

Custom harnesses (`custom:<cmd>`) and built-in CLIs use this same contract. BYOA does not get a second prompt shape.

Full seeding walkthrough: [master-howto.md](master-howto.md) Step 4.

## 3. Update

```sh
tickets brief --role backend --show          # print live role context
tickets brief --role backend "standing fact"   # append a timestamped line
tickets brief --role backend --file roles/backend.md   # replace whole file
tickets brief $TICKET_AGENT "seat-only note"   # per-seat brief
```

`--role _shared` is refused — edit `.tickets/briefs/_shared.md` directly. Exactly one target: agent name, `--role`, or `--ticket`.

The next wake must see the new text. Re-join is not required.

## Shared vs role vs agent

| Layer | File | Who edits | Example |
|---|---|---|---|
| Shared | `_shared.md` | Lead / operator | One ticket; advisory decisions; fewest turns in the UI |
| Role | `roles/<role>.md` | Anyone on that lane | Owns API/SDK; leaves console alone |
| Agent | `briefs/<agent>.md` | That seat | Worktree path, model, a one-line standing note |

Put lane facts in the role file so two backend seats stay aligned. Put seat-only facts in the agent brief.

## Checklist (first seat)

- [ ] Unique `TICKET_AGENT`; `tickets join … --roles <lane>`
- [ ] `.tickets/briefs/_shared.md` present or prompt says empty
- [ ] `.tickets/briefs/roles/<role>.md` present or prompt says empty
- [ ] Know `tickets brief --role <role> --show` for later updates
- [ ] Custom harness uses the same prompt file
- [ ] Not treating Atman as a memory brain or graph-docs product
