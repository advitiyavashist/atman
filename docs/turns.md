# Turns (schema v1)

The agent-facing objective is **task completion in the fewest turns**.
`tickets turns` is the metric the optimizer (T-313) reads. This file freezes
what a turn is and the `--json` shape. Do not rename keys.

## What is a turn

**One completed watch run = one turn.** In the trajectory log that is a
`run_start` followed by its `run_end`. Incomplete starts (no `run_end`) do
not count.

A **ticket's turns** are those runs from the first `claim` to the final
`done` (or `merge`). A `reopen` does not reset the counter: later runs are
added.

This is the **board's** turn count, not the harness `num_turns` field on
`run_end`. Harness-reported turns stay on `tickets trajectories --summary`.

## Unknown is not zero

T-311: a field the writer does not know is omitted, never defaulted.

Backfill synthesises `claim` / `update` / `review` / `done` from ticket
files. It **cannot** recover watch runs. For those tickets `tickets turns`
reports `"turns": null` (table: `-`), never `0`. Aggregates (mean / median)
use only tickets that have at least one `run_end`. `aggregates.n_unmeasured`
is how many rows were excluded for that reason.

## Command

```sh
tickets turns
tickets turns --ticket T-312
tickets turns --agent grok-worker --model grok --epic E-011 --since 2026-09-01
tickets turns --json
```

Table columns: ticket, owner, model, turns, wall-clock, reopens, stuck, outcome.

- **owner** — ticket owner, else the last claim/run agent
- **model** — last model on the ticket's events, else `tickets join --model`
- **turns** — `run_end` count, or `-` / `null` if none
- **wall-clock** — first `claim` (else first event) to last `done`/`merge` (else last event)
- **reopens** — `reopen` events
- **stuck** — messages whose text starts with `stuck` and `--re` that ticket
- **outcome** — last of done / merge / review / block / reopen, else ticket status

## Frozen `--json`

```json
{
  "v": 1,
  "tickets": [
    {
      "ticket": "T-001",
      "owner": "alice",
      "model": "opus",
      "turns": 3,
      "wall_clock_s": 3600.0,
      "reopens": 1,
      "stuck": 0,
      "outcome": "done"
    }
  ],
  "aggregates": {
    "mean": 3.0,
    "median": 3.0,
    "n": 1,
    "n_unmeasured": 0,
    "by_agent": [{"agent": "alice", "n": 1, "mean": 3.0, "median": 3.0}],
    "by_model": [{"model": "opus", "n": 1, "mean": 3.0, "median": 3.0}],
    "by_role": [{"role": "backend", "n": 1, "mean": 3.0, "median": 3.0}],
    "by_priority": [{"priority": 2, "n": 1, "mean": 3.0, "median": 3.0}]
  }
}
```

Row keys are exactly: `ticket`, `owner`, `model`, `turns`, `wall_clock_s`,
`reopens`, `stuck`, `outcome`. `turns` / `model` / `owner` / `wall_clock_s` /
`outcome` may be JSON `null` when unknown. Aggregates omit unmeasured tickets
from mean/median; `n_unmeasured` counts them.

UI panel (worst-10, per-agent medians on the status page) is a follow-up —
this ticket is CLI-first.

## Source

Events come from `.tickets/trajectories.jsonl` (and dated archives). Schema:
`docs/trajectories.md`. Implementation: `src/ticket_board/turns.py`, wired from
root `tickets.py` and `src/ticket_board/cli.py` (same command, same JSON).
