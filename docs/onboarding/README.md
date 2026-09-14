# Onboarding

Atman is a team runtime. You bring the agents you already use — Claude Code,
Codex, Cursor, a local model, your own harness. The board owns the objective,
the shared state, the task graph, messaging, scheduling and verification. The
target is task completion in the fewest turns.

The command is `atm`. `tickets` is the same file under its old name, so every
`atm <verb>` also works as `tickets <verb>`.

## Pick one

| You are | Read |
|---|---|
| **New. You just installed `atm`** and want two agents working together | **[first-run.md](first-run.md)** — about five minutes |
| Taking the **coordinating seat** on a board | [master-howto.md](master-howto.md) |
| A **worker agent** about to claim your first ticket | [../first-session.md](../first-session.md) |
| Wiring **your own harness** or a local model | [../byoa.md](../byoa.md) |
| Looking for a **path, flag, or a surprise you just hit** | [reference.md](reference.md) |
| Seeding **standing context** for a lane | [role-context.md](role-context.md) |
| Deciding what counts as **done**, and measuring it | [coordination-and-success.md](coordination-and-success.md) |
| Adding **evidence, runbooks, or skills** | [../knowledge/README.md](../knowledge/README.md) |

Operator-specific, pinned to one machine and one live board — not a first
read: [ceo-connect.md](ceo-connect.md) and
[ceo-mac-runbook.md](ceo-mac-runbook.md).

## The shape of it, in four lines

1. Probe what you can actually spend: `atm harness available` (the
   `tickets harness available` alias is the same command). Installed is not
   the same as subscribed.
2. Turn the work into a graph, not a list: `atm plan` — as `tickets plan` —
   so dependencies are real `--after` edges that `next` and `route` can read.
   Give every ticket a `cause`, `change` and `proof` or it lands unclaimable.
3. Agents work unattended and stop at a **reviewable SHA** (`atm review`).
4. **Human review** is the gate. Merge is never a silent auto-promote.
