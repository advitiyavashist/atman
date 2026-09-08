# Turns (schema v1)

The agent-facing objective is **task completion in the fewest turns**.
`tickets turns` is the metric the optimizer (T-313) reads. This file freezes
what a turn is and the `--json` shape. Do not rename keys.

## What is a turn

**One completed productive watch run = one turn.** In the trajectory log that
is a `run_start` followed by its `run_end`. Incomplete starts (no `run_end`)
do not count.

T-425/T-481: a `run_start`/`run_end` pair increments `turns` only when THAT
run recorded a bound-ticket write (claim, update, review, done, block,
reopen, or `msg --re` that ticket). The pairing key is `run_id` when present,
else `(agent, run_no)` (Cursor-harness rows often carry `run_no` only). Idle
pulses, session-limit fails, and generic `exit=1`/`timed_out` with no ticket
write stay in jsonl but do not increment (HB87/HB88: no OR-nonzero).
Broadcasts without `--re` never increment. The FLAG is `bound_write` on
`run_end` (and matching `run_id` or `(agent, run_no)` on the write events),
not a grep of message text for `idle:`. Missing `bound_write` does not
default to "count" when a pairing key exists *and writes carry that key
type*. A `run_no`-only `run_end` whose trajectory has no write events
bearing `run_no` is unpairable (historical writers never stamped it) and
takes the pre-T-425 count path — decided from the log, not a date. Only
events with neither `run_id` nor `run_no` (pre-T-425) still count every
completed run. No backfill of existing jsonl.

A **ticket's turns** are those productive runs from the first `claim` to the
final `done` (or `merge`). A `reopen` does not reset the counter: later runs
are added.

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

Table columns: ticket, owner, model, turns, wall-clock, cost, tok in, tok out,
reopens, stuck, outcome.

- **owner** — ticket owner, else the last claim/run agent
- **model** — last model on the ticket's events, else `tickets join --model`
- **turns** — productive `run_end` count (T-425 FLAG), or `-` / `null` if none
- **wall-clock** — first `claim` (else first event) to last `done`/`merge` (else last event)
- **reopens** — `reopen` events
- **stuck** — messages whose text starts with `stuck` and `--re` that ticket
- **outcome** — last of done / merge / review / block / reopen, else ticket status
- **cost** — summed `cost_usd` over the ticket's `run_end` events, or `-` / `null`
  if no run reported one (T-396)
- **tok in / tok out** — summed `tokens_in` / `tokens_out`, same rule

### Cost is measured separately from turns

A ticket can have measured **turns** and unmeasured **cost** at the same time,
and today that is the normal state of the whole board: a turn is a `run_end`,
which the watcher always writes, while a cost exists only when the harness
reported one. So `aggregates.cost` carries its own `n` / `n_unmeasured` rather
than reusing the turns counts.

`-` in the cost column means **unmeasured, not $0.00**. Rendering an unreported
cost as zero would make the agent we know least about look like the cheapest
one — which is exactly the decision `tickets route` is being built to make.

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
      "outcome": "done",
      "cost_usd": 0.25,
      "tokens_in": 11,
      "tokens_out": 22
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
    "by_priority": [{"priority": 2, "n": 1, "mean": 3.0, "median": 3.0}],
    "cost": {"n": 1, "mean": 0.25, "median": 0.25, "total": 0.25, "n_unmeasured": 0},
    "cost_by_agent": [{"agent": "alice", "n": 1, "mean": 0.25, "median": 0.25, "total": 0.25}],
    "cost_by_model": [{"model": "opus", "n": 1, "mean": 0.25, "median": 0.25, "total": 0.25}]
  }
}
```

Row keys are exactly: `ticket`, `owner`, `model`, `turns`, `wall_clock_s`,
`reopens`, `stuck`, `outcome`, `cost_usd`, `tokens_in`, `tokens_out`. `turns` /
`model` / `owner` / `wall_clock_s` / `outcome` / `cost_usd` / `tokens_in` /
`tokens_out` may be JSON `null` when unknown. Aggregates omit unmeasured
tickets from mean/median; `n_unmeasured` counts them.

T-396 added `cost_usd`, `tokens_in`, `tokens_out` and the three `cost*`
aggregates. The addition is **additive**: `v` stays `1`, no existing key was
renamed, removed or reordered, and the pre-existing keys above are still
present on every row.

Console (T-372): home hero reads `aggregates.median`; the turns-efficiency
panel shows worst-10 + per-agent medians from this same object. Do not
reshape `--json` for the UI.

## Shadow route ranking (T-415)

`tickets route --shadow` picks the `(agent, model)` cell with enough support
(`n` ≥ 5 measured turns) that finished comparable tickets (same role and
priority band) in the **fewest median turns**, tie-broken by **lowest median
`cost_usd`** among cells that actually reported cost. `cost_usd` null is
**unmeasured**, never treated as $0.00 — an agent with unknown cost cannot win
a cost tie-break against one with measured cost. When every learned candidate
for a ready ticket has `n_cost=0`, the command still prints the turns-only
pick and labels it `cost: unmeasured (n=0)`. `--by cost` inverts the axes
(cost first, then turns) for comparison. Shadow mode assigns nothing; it only
prints and appends `shadow_decision` events.

## Source

Events come from `.tickets/trajectories.jsonl` (and dated archives). Schema:
`docs/trajectories.md`. Implementation: `src/ticket_board/turns.py`, wired from
root `tickets.py` and `src/ticket_board/cli.py` (same command, same JSON).
