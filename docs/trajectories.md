# Trajectories (schema v1)

`.tickets/trajectories.jsonl` — one JSON object per line, append-only, written
by the commands that already know a fact.

This is the team's core data asset. The agent-facing objective is **task
completion in the fewest turns**, and nothing can optimise a number nobody
records. Every field here exists to make some part of that number computable:
how many watch runs a ticket cost, how many updates and messages its agent
needed, how often it came back, and — when the harness reports them — how many
model turns, tokens and dollars went into it.

## The rule that shapes the whole schema

**A field the writer does not know is omitted, never defaulted.**

A reader must be able to tell `tokens_in: 0` (the harness said zero) from a
missing `tokens_in` (the harness never said). Only the first is a number. This
is why there is no `"model": "unknown"`, no `"cost_usd": 0` fallback and no
token count estimated from output length: a plausible-looking guess is
indistinguishable from a measurement once it is on disk, and the cost/quality
learning layer this file feeds cannot un-mix them later.

## Privacy

Ids, counts, outcomes and timings only.

- No prompt text, no tool inputs, no tool results.
- No note bodies and no message bodies — their **length** is recorded
  (`notes_len`, `text_len`) and nothing else.
- `msg` events carry `from` (as `agent`), `to`, `re` (as `ticket`) and a length.
- `trigger` on a run is the *keys* of `pending_work()`, never its values: the
  values are message text and ticket titles.

## Envelope (every event)

| field | always | meaning |
|---|---|---|
| `v` | yes | schema version, currently `1` |
| `at` | yes | UTC ISO-8601, second precision. On a backfilled event this is when the thing **happened**, not when backfill ran (see `backfilled_at`). |
| `kind` | yes | one of the kinds below |
| `agent` | when known | the agent that performed the action |
| `ticket` | when the event has one | `T-001` |
| `harness` | when registered | `tool` from `tickets join` (`claude`/`codex`/`cursor`/…) |
| `model` | when registered | `model` from `tickets join` |
| `effort` | when registered | reasoning effort, if the workforce entry carries one |
| `epic`, `sprint` | when the ticket has them | |
| `repo` | when the ticket has a pin | repository id recorded on the ticket |
| `objective_id` | when an objective is set | `obj-<sha1(text|at)[:8]>` — see below |

### `objective_id`

`tickets objective` stores no id, and minting one would rewrite a file other
seats read. So the id is **derived**: a short hash of the objective's text and
set-time. The same objective yields the same id in every agent and every
process; editing the objective starts a new id, which is what a reader grouping
trajectories by objective wants.

### `worktree` / `branch` / `sha`

These mean **the tree this event was recorded from** — for `review` and `done`
that is the deliverable tree (`--artifact` when passed), for everything else the
process cwd. They are *not* the ticket's pinned branch/sha: on this board an
agent routinely drives the CLI from one repo while the deliverable lives in
another (T-272), so conflating the two would reintroduce exactly that bug in the
metrics layer. The pin, where one exists, is the separate `pin` field
(`branch@sha`). When git cannot resolve a tree, all three are omitted rather
than filled with placeholders (T-259: unresolved must mean absent, not `"?"`).

## Kinds

| kind | written by | notable fields |
|---|---|---|
| `run_start` | `tickets watch` | `run_no`, `trigger` (sorted pending keys), `harness_cmd`, `worktree` |
| `run_end` | `tickets watch` | `started_at`, `ended_at`, `duration_s`, `exit`, `timed_out`, `outcome` (`limit` when the run's own log slice matched a CLI limit string), plus harness usage when reported |
| `claim` | `try_claim` | `state_before: open`, `state_after: claimed` |
| `update` | `tickets update` / `note` | `notes_len`, `state_after` |
| `review` | `tickets review` | `outcome: review`, `pin`, `notes_len`, `active_hours` |
| `done` | `tickets done` | `outcome: done`, `pin`, `active_hours`, `wait_hours` |
| `block` | `tickets block` | `outcome: blocked`, `state_before` |
| `reopen` | `tickets reopen` | `outcome: reopened`, `prev_owner` |
| `merge` | `tickets merge` | `pin`, `merged_as`, `trunk`, `prev_owner` |
| `msg` | `post_message` | `to`, `text_len` |
| `shadow_decision` | `tickets route --shadow` | print-only route: `source` (`prior`/`learned`), `rule_agent`, `learned_agent`, `n`, `n_unmeasured`; `expected_turns` omitted when unknown. Never an assign. See `docs/scheduler.md`. |

`claim` is written inside `try_claim()` rather than in `cmd_next`/`cmd_claim`,
because that function is the single point where a claim actually succeeds — a
future third claim path cannot be added without the event coming with it.

### Harness usage (`run_end`)

`claude -p --output-format json` ends a run with one JSON object carrying
`usage`, `total_cost_usd`, `num_turns` and `duration_ms`. When the operator has
asked for that format, those become `tokens_in`, `tokens_out`,
`tokens_cache_read`, `tokens_cache_write`, `cost_usd`, `turns` and
`harness_duration_ms`. When they have not — which is the default watch command —
none of those fields appear at all.

The parse reads only **this run's own slice** of `agents/<name>.watch.log`,
between the log size taken before the run and the size after. If the log shrank
mid-run (rotation, or an operator truncating it) the slice is discarded rather
than re-read from zero, because a previous run's result object would otherwise
have its tokens billed to this one.

`harness` (what the agent registered) and `harness_cmd` (what the watch command
line actually invoked) are separate fields on purpose. They disagree exactly
when an agent is running under a different tool than it joined as, and that
disagreement is a finding, not noise.

## Reading

```sh
tickets trajectories                       # last 200 events
tickets trajectories --ticket T-213 --summary
tickets trajectories --agent opus-backend-2 --kind run_end --since 2026-09-01
tickets trajectories --json --limit 0      # everything, machine-readable
tickets trajectories export --out /tmp/traj.jsonl   # same filters apply
```

`--summary` is the turns view: per ticket, `runs` (watch runs that reached
`run_end` — the board's own turn count), `turns` (what the harness itself
reported, `-` when it reported none), updates, messages, reopens, the agents
involved and the outcome.

## Backfill

```sh
tickets trajectories backfill --dry-run
tickets trajectories backfill
```

Synthesises `claim`, `update`, `review` and `done` from what the board already
records, so tickets finished before this log existed are usable. Two rules keep
a re-run honest:

1. Every synthesised event carries `src: "backfill"` and a deterministic
   `bf_key` (`<ticket>:<kind>:<at>`), so running backfill twice writes nothing
   new.
2. Backfill never writes into the instrumented era, under two guards, because
   the two kinds of event fail differently:
   - `claim`, `review` and `done` happen at most once per ticket, so if a *live*
     writer already recorded one, backfill refuses that kind for that ticket
     outright. A timestamp comparison is not enough here: `claimed_at` is
     rewritten in place on a re-claim, so a ticket whose stored claim time
     predates the log would be counted once by the writer and once by the
     reader, and a turns metric built on it would be silently inflated.
   - `update` happens many times per ticket, so it is cut at the *floor*
     instead: the earliest instant a live writer recorded for that ticket. The
     pre-instrumentation half of a ticket's note history is recoverable; the
     instrumented half is already in the log.

### What backfill deliberately cannot recover

- **No `run_no`, `exit`, tokens or cost.** No such record was ever kept, so
  those fields are absent rather than zeroed.
- **No `block` events.** The board stores a block *reason* as a note but never a
  block timestamp; `updated` only means "when this file was last written by
  anything". Dating a block from it would reorder the very sequence a turns
  metric reads.
- **`claim` is attributed to the ticket's *current* owner.** A ticket that was
  reopened and re-claimed by someone else has only its latest owner on disk, so
  its synthesised claim names that agent. `update` events do not share this
  limitation: each note carries its own `by`, so a second lane on a ticket
  (T-238) shows up correctly in the backfilled history.
- **`run_end` events are never synthesised**, so a backfilled ticket shows
  `runs = 0`. That is the honest reading: nobody counted its runs.

## Rotation

Rotates to `trajectories.<YYYY-MM-DD>.jsonl` at 50 MB
(`TICKETS_TRAJECTORIES_MAX_BYTES`), by the same swap-under-a-lock as
`messages.jsonl`. Unlike the message reader, **every** trajectory reader
includes archives by default: these readers are analytical, and a silently
truncated history corrupts an answer rather than merely delaying a message.

## Two entry points, one log

This tool ships along two paths and both write here:

| path | file | how it runs |
|---|---|---|
| the live shim, `install.sh`, `tickets watch` | `tickets.py` (repo root) | shipped as a lone file, no package beside it |
| `pip install` → the `tickets` console script | `src/ticket_board/cli.py` | `[project.scripts] tickets = "ticket_board.cli:main"` |

Instrumenting only the root script would not leave the packaged CLI merely
uninstrumented — it would leave **this log wrong**. A board driven by both (a
watcher on the root script, an operator on the installed console script) yields
a file with silent holes, and a metric cannot tell a hole from a real zero. An
absent log is honest; a partial one is not. So the packaged path writes the
same events, with two consequences worth knowing:

- **`run_start` / `run_end` come only from the root script.** `cmd_watch` does
  not exist in `cli.py`, so runs are counted by the watcher alone. This is a
  real absence, not a hole: nothing else spawns a run. The packaged CLI still
  serves `tickets trajectories` (list / export / backfill) against the same
  file.
- **`branch` / `sha` are omitted more often on the packaged path.** Its
  `git_state()` fills those with `"?"` where the root script returns `None`
  (T-259). A `"?"` on disk reads back as a real branch name, so the packaged
  writer drops the field instead — the omit-never-default rule, applied to a
  placeholder that would otherwise have looked like a measurement.

The writers themselves live in `src/ticket_board/trajectories.py`, which
`cli.py` imports. The root `tickets.py` **cannot** import it — it ships as a
single file with no package to import from — so it carries its own copy of the
same logic. That duplication is only safe while something goes red when it
drifts, which is what `tests/test_trajectories_entrypoints.py` is for: it
compares the two writers' version, kinds, rotation ceiling, derived
`objective_id`, harness lookup and full emitted record, field by field rather
than against a hand-written list of expected keys. A field added to one copy
and forgotten in the other fails there without anyone remembering to update the
test.

## Failure policy

All writes are best-effort and swallow their errors. Every writer sits on a
command's success path — the claim has happened, the ticket is already saved —
so an exception raised by instrumentation would abort work that already
occurred. A missing trajectory line is a gap in a metric; a raised one is a lost
ticket transition.

## Environment

| variable | default | |
|---|---|---|
| `TICKETS_TRAJECTORIES_MAX_BYTES` | 52428800 | rotation threshold |
| `TICKETS_TRAJECTORIES_SCAN_BYTES` | 262144 | max bytes of a run's log slice scanned for harness usage |
