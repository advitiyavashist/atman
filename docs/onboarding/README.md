# Onboarding

Pick one line. You do not need the others yet.

| If you are… | Read |
|---|---|
| **Installing `atm` for the first time and want a working board** | **[first-run.md](first-run.md)** |
| Taking the **master** seat on a board that already exists | [master-howto.md](master-howto.md) |
| A worker about to claim your first ticket | [../first-session.md](../first-session.md) |
| Looking up a path, a flag, or something that just refused you | [reference.md](reference.md) |
| Registering a custom or local harness | [../byoa.md](../byoa.md) |
| Seeding standing context per role (know → inject → update) | [role-context.md](role-context.md) |
| Coordinating an already-staffed team day to day | [coordination-and-success.md](coordination-and-success.md) |
| Adding evidence, runbooks, or skills | [../knowledge/README.md](../knowledge/README.md) |
| The operator on this specific Mac, with pinned folders | [ceo-mac-runbook.md](ceo-mac-runbook.md) |
| Wiring the CEO seat to Cursor / Codex | [ceo-connect.md](ceo-connect.md) |

If you installed `atm` five minutes ago, the answer is the first row.

Atman is a **team runtime**. You bring the harnesses. The board owns the
objective, the shared state, the task graph, messaging, scheduling, and
verification. The north star is **task completion in the fewest turns**.

The shape of a run, in order: probe what you can actually spend
(`atm harness available`), lay out work as a graph with real `--after`
edges (`atm plan`, or `atm create --deps` on a first board), let
seats persist unattended to a **reviewable SHA**, and gate it on
**human review**. [first-run.md](first-run.md) does exactly that, end to end,
with the output it printed.

Install today is `git clone` + `./install.sh` on macOS. Homebrew, Linux
packages, and pipx are planned and not published — see
[reference.md](reference.md#install-modes).
