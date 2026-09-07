# The managed Claude runner (T-188)

A managed agent is one whose Claude sessions are started by a local supervisor
rather than by a person at a terminal. This is how a task message becomes an
actual run.

Two halves, both in this repo:

| Half | Where | What it is |
|---|---|---|
| Board | `src/ticket_board/server/app.py` + `storage/messaging.py` | `/runners/register`, `/runners/jobs`, `/runs/{id}/events`, `/runs/{id}/cancel`, and the leasing rules behind them |
| Supervisor | `src/ticket_board/runners/` | A local process that holds a lease, consumes wake jobs and runs `claude` |

## The order of operations, and why it is that order

```
register (fenced)     two supervisors for one agent is the fatal case
  -> reconcile        a crash between claim and spawn must not silently re-run
  -> lease a job      the board refuses to lease while a run is live
  -> dedupe locally   at-least-once delivery, exactly-once execution
  -> retain the run   session id recorded BEFORE the process exists
  -> claim the ticket "started" is a lie without a valid claim
  -> spawn            stdin, allowlisted cwd, stripped environment
  -> report started   only now: the process exists and the claim held
  -> wait in budget   a budget pause is visible, never a silent hang
  -> report outcome   responded / failed / budget_reached, with a reason
```

## Operator commands

```sh
# Connect the project first (T-181's flow) -- this is a separate step.
python -m ticket_board.adapters.claude.connect connect --project-dir ./scratch ...

# Then check what the runner would actually be able to do.
python -m ticket_board.runners status --project-dir ./scratch

# And supervise.
python -m ticket_board.runners run --project-dir ./scratch \
    --worktree ./scratch --permission-policy prompt
```

`status` exits `0` healthy, `2` unhealthy, `1` for "this project is not
connected". It resolves the `claude` binary as well as reading the state file,
because **installed and runnable are different facts** — T-181 lost a live
session to a hook command spelled `python` on a machine that only has
`python3`, and every status field read healthy the entire time.

## The numbers, and where they come from

| Number | Value | Why |
|---|---|---|
| `RUNNER_LEASE_SECONDS` | 120 | Long enough that an ordinary poll cycle never drops the lease; short enough that a dead supervisor's agent is recoverable in about two minutes. |
| `RUNNER_POLL_INTERVAL` | 0.05s | The acceptance target is a start within 5s of a committed message. A 1s interval would spend a fifth of that budget before the supervisor even sees the job. |
| `WAKE_LEASE_SECONDS` | 120 | Matches the runner lease: a job outliving its supervisor's lease would be reclaimed by a runner that no longer exists. |
| `WAKE_MAX_ATTEMPTS` | 3 | At-least-once, bounded. After three the job enters the inspectable failed queue rather than retrying forever or being dropped. |
| `LEDGER_CAP` | 500 | The local dedupe ledger is bounded so a long-lived runner's state file cannot grow without limit. |
| Budget defaults | 3 hops / 10 turns / 15 min | The design doc's pilot defaults, carried unchanged. Only `max_seconds` is enforced here; see below. |

## Measured, and not measured

`tests/runners/test_idle_start_latency.py` measures **commit-to-started**: a
task message committed to the board, a long-polling idle supervisor noticing
it, the claim, the spawn and the `started` report. On this machine that is
**~0.02s**.

It does **not** measure Claude's own startup — the child is a fake process.
Including a real model launch would be measuring Anthropic's availability and
this machine's load, and this machine runs several live Claude agents. So the
honest claim is: *the board and supervisor overhead leaves essentially the whole
5s target for Claude itself.* The 5s figure in the design doc remains a target,
not an observed end-to-end result.

## Rules that are enforced, not merely intended

**Fencing.** `RunnerLease` is compare-and-swapped on `epoch`. A second
supervisor for the same agent gets `409 run_already_active`. A restarted
supervisor reuses its `runner_id` from the state file — minting a fresh one
would fence out a supervisor that may still be alive.

**Concurrency 1.** The board hands out no job while the agent has a run in
`starting`/`running`/`paused`, or a job still leased. A queued task waits
behind the current claim; it does not start a parallel session.

**At-least-once delivery, and what that does and does not license.** Jobs are
deduplicated on `(message_id, recipient_agent_id)` at creation and again in the
supervisor's local ledger. When a lease expires the answer depends on how far
the run got:

- still `pending` — nothing was spawned, so nothing outside the board happened.
  The job is requeued and the same run is reused. An honest retry.
- past `pending` — a session was retained and a process was probably started.
  Whether it edited files, pushed a commit or replied to anyone is now
  unknowable. The run **fails with that stated** and is **not retried**. This
  is the design doc's uncertain-side-effects rule: re-running a task that may
  have half-finished is worse than telling a human it is unclear.

A `paused` run is the exception to both: it is waiting for a *person*, so it is
never reaped as an orphan. It holds the agent until an operator cancels it
(`POST /runs/{id}/cancel`). Letting the agent move on to the next job while an
approval was outstanding would be a way to bypass that approval by waiting.

This is why the system does not promise exactly-once shell execution, and says
so rather than implying otherwise.

**The message body never reaches a command line.** It is written to the child's
stdin, with `shell=False` and a real argv. A task message is arbitrary
operator- and agent-authored text; quoting it into a shell string is command
injection with extra steps.

**The child never inherits this agent's board identity.** `TICKET_AGENT`,
`TICKET_BOARD`, `TICKETS_DIR` and `CLAUDE_SESSION_ID` are stripped from the
child environment. A spawned Claude inherits its parent's environment, and a
`TICKET_AGENT` in it makes the child's own Stop hook post to this board under
the supervisor's name. T-214 hit exactly this.

**Permissions are the operator's, not the supervisor's.** `permission_policy`
comes from the lease. `prompt` maps to no flag at all, so a permission request
surfaces and pauses the run with `needs_approval` — the supervisor never
answers one on the operator's behalf.

**Two sessions, never conflated.** `POST /runs/{id}/events` involves both:

- the *reporter's* session — the supervisor's, bound from its agent credential
  and never read from the request body. This is the identity claim, and frozen
  T-178's actor-bound-from-credential governs it.
- the *child* session — the Claude conversation the run executes in. No
  credential can carry it, because it does not exist until the supervisor mints
  it. So it arrives in the body and is bound the other way: **write-once**.

Attribution follows T-239's three states. An event from a reporter with no live
session is recorded, denied all effect on the run's liveness (no state change,
no `started_at`, no version bump) and audited as `run.event.unattributed`; one
from a revoked session, or one contradicting the run's retained child session,
is audited as `run.event.superseded`. An anonymous caller cannot paint a run
green or close it. A supervisor that genuinely lost its session recovers
through the lease — it expires and a supervisor registers at a new epoch — not
by closing the run anonymously.

## What the installed CLI actually accepts

Checked against `claude --version` / `claude --help` on **Claude Code 2.1.263**,
not against the documentation, because the two disagreed:

| What | Reality |
|---|---|
| `--session-id` | Takes a **UUID** — "must be a valid UUID", enforced. The board's `SessionId` is `^ses_[0-9a-z]{8,32}$`. **Two different id spaces.** `state.board_session_id()` converts a UUID to the board form (`ses_` + 32 hex, which satisfies the pattern) and `runtime_session_id()` converts back. An earlier draft of this module minted a `ses_...` and handed it straight to the CLI; that run would never have started. |
| `--max-turns` | **Does not exist.** It was in an earlier draft, taken from the shape of `RunBudget` rather than from the binary, and would have died at exec. The turn budget is carried and reported but **not enforced** by this supervisor — only the time budget is, because only the time budget can be. |
| `--permission-mode` | Choices are `acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`, `plan`. `allowlist` maps to `acceptEdits`, `deny_all` to `plan`, and `prompt` to no flag at all. |

`runners.launcher.preflight()` re-asks the binary these questions and
`python -m ticket_board.runners status` reports the answer, because *runnable*
and *compatible* are as different as *installed* and *runnable* — and a
supervisor that dies at exec leaves nothing to read.

## What this lane does not decide

**Who gets woken.** Wake jobs are created by the messaging lane (T-187), which
is also where "a reply does not wake its author" and the agent-to-agent hop
budget live. A supervisor that inferred recipients would be a second, quieter
routing policy. `max_hops` is carried on `RunBudget` and passed through, but
nothing here counts hops, because nothing here creates the messages that would
increment them.

**End-to-end verification.** T-190 verifies real message-to-task execution
against merged T-187. These tests build against the frozen contract and its
published fixtures, which is what the freeze is for; they do not stand in for
that.
