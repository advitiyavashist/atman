# Runbook: message-to-task execution, verified (T-190)

What `tests/acceptance/messaging/` proves about the messaging (T-187),
managed-runner (T-188) and messages-UI (T-189) lanes when they are actually
run together, what it found, and how to re-run the one proof that costs a
model call.

## What this verifies against

At the time of writing none of the three lanes was on `tickets main`
(`origin/main` = `a401ac3`, no `/messages` or `/runners` routes). The steer
board recorded them "merged", but that is a steer-side delivery record, not an
integration into this repo (`docs/INTEGRATION.md`). So this branch,
`cursor-demo/t190-messaging-acceptance`, is cut from `origin/main` and merges
the three feature branches by hand:

| lane  | branch@sha                                      | merge commit |
|-------|-------------------------------------------------|--------------|
| T-187 | `opus-backend-2/t325-t187-fixes@8e364e9`        | `375dd44`    |
| T-188 | `opus-infra/t188-managed-runner@1d11f50`        | `982b72f`    |
| T-189 | `sonnet-console/t189-messages-ui@47a2f68`       | `58d7431`    |

Two conflicts, both in `src/ticket_board/server/app.py` and
`storage/messaging.py`: each lane had stubbed the other's routes with
`_not_this_lane`. Resolution: drop the stubs, keep both real route tables.
Nothing under `docs/contracts/` or `tests/fixtures/` was touched.

**Consequence for the verdict:** every PASS below is a pass against this
synthetic integration, not against `tickets main`. It becomes a pass for main
only when main contains these three lanes (or their successors) and the suite
is re-run there. The ticket title says the same: no PASS until main has routes.

## How the tests work

Every test drives the real router over a real loopback socket
(`ticket_board.server.httpd.serve`, a reserved port because `serve(port=0)`
builds its CSRF allowlist before binding), through the real `RunnerClient`
and the real `Supervisor`. Operator calls are `urllib` with the bootstrap
cookie + CSRF token; agent calls carry the bearer the enrollment exchange
returned. Exactly one thing is substituted: the `claude` child process
(`FakeLauncher` / `FakeProcess` in `live_board.py`). Two tests remove even
that -- see "Real children" below.

```
python3 -m pytest -q tests/acceptance/messaging          # ~25 s, no model call
```

Findings are `xfail(strict=True)`. They fail today for the reason in the
marker; the day the code is fixed they turn into an XPASS failure and someone
has to promote them to plain tests. A finding cannot silently become a pass.

## Real children

`test_dispatch_end_to_end.py::test_the_real_launcher_completes_a_run_with_a_real_child_process`
uses the real `ClaudeLauncher` with `CLAUDE_BIN` pointed at a two-line shell
script (`cat`). No model, real `Popen`, real pipes. This is the test that
would have caught F-12 before any model was spent.

`test_live_claude.py` spends one real `claude -p` call. It is skipped unless:

```
env -u TICKET_AGENT T190_LIVE_CLAUDE=1 \
    T190_LIVE_RECEIPT=/tmp/t190-live-receipt.json \
    python3 -m pytest -q -s tests/acceptance/messaging/test_live_claude.py
```

`env -u TICKET_AGENT` is belt-and-braces: the launcher strips it anyway, but
five live agents share this machine and the Stop hook in `~/.claude` posts to
the steer board when it sees one. Disposable means: session UUID minted by the
supervisor, worktree under pytest's tmp dir, `~/.claude` untouched by the
test. Claude Code still writes its own transcript for the session under its
project directory; that is the one artifact left behind.

The receipt of the passing run is committed at
`docs/acceptance/T-190-live-receipt.json`: claude 2.1.263, `-p --session-id
<uuid>`, spawn 0.004 s, wall 4.92 s, nonce sent on stdin came back on stdout,
run `responded`, ticket `T190-1` claimed by the agent, delivery `delivered`,
**no reply on the board** (F-2, observed live).

## The acceptance list, item by item

Design source: `docs/messages-and-runners.md` (tickets-design). "PASS" means
the integrated code does it and a test proves it; "FINDING" means it does not,
and a strict xfail pins the gap.

| acceptance item | verdict | evidence |
|---|---|---|
| Task DM to idle managed Claude -> claim -> real run start | PASS | live receipt; `test_a_dm_task_wakes_claims_runs_and_reports_over_a_real_socket` |
| ... -> reply in the original thread | FINDING F-2 | `test_the_agents_reply_lands_in_the_original_thread`; live receipt `reply_posted_to_board: false` |
| Receipt chain Sent->Queued->Delivered->Started->Responded | FINDING F-1 | `test_the_receipt_reaches_responded_when_the_run_does` |
| Human messages alone do not fan out | PASS | `test_a_plain_dm_or_channel_message_wakes_nobody` (3 runners polling, 0 jobs, 0 deliveries) |
| Channel mention wakes the named agent | FINDING F-6 (pinned as shipped) | `test_a_mention_delivers_to_the_named_agent_only_and_does_not_wake` -- a mention is a `queued` delivery, no wake job |
| Channel master routing (`via_master`) | PASS | `test_via_master_wakes_the_designated_master_once_and_nobody_else` |
| Duplicate delivery (retried Send task; redelivered wake job) | PASS | `test_a_retried_send_task_wakes_the_agent_once`, `test_a_redelivered_wake_job_is_recognised_and_not_executed_twice` |
| Crash replay (restart between claim and spawn) | PASS | `test_a_restart_between_claim_and_spawn_is_reviewed_not_rerun` -- reviewed, not rerun |
| Interactive-session / process collision | PASS | `test_a_supervisor_that_finds_the_previous_process_alive_does_not_double_start`, `test_a_fresh_job_is_never_started_with_resume` |
| Busy: concurrency 1, second task waits | PASS (queue) / FINDING F-4 (reason) | `test_a_busy_agent_has_no_parallel_run_and_the_second_task_waits`; no `agent_busy` receipt |
| Offline: no runner lease | FINDING F-3 | receipt says `delivered` with no runner registered; no `runner_offline` |
| Hook-only agent | PASS | `manual_resume_required`, no wake job |
| Second task after a finished run, `max_active_tickets=1` | FINDING F-5 | `capacity_exhausted` instead of queueing |
| Spoofed author on both write routes | PASS | `sender_identity_rejected` |
| Private channel / DM visibility | PASS | non-member 403 on read and write; DM readable by its two participants only |
| Cross-project credential | PASS | other project's operator and agent get 403/404, never data |
| Revoked member | PASS | `membership_revoked` on read and send |
| Revoked session lease stops the runner | PASS | register/lease/run_event all 401 after revoke; run never starts |
| Revocation cancels the pending dispatch | FINDING F-7 | wake job stays `pending`, delivery stays `delivered` |
| Dependency block: never pretend work started | PASS | claim refused `dependency_unmet`, run `failed` with `started_at` null, no process |
| Dependency block: "Waiting on <ticket>" receipt | FINDING F-8 | receipt `delivered`, wake job minted, lands in FAILED as a failure |
| Permission request: board side (`needs_approval` pauses, holds the agent, operator cancels) | PASS | `test_a_needs_approval_run_holds_the_agent_until_an_operator_acts` |
| Permission request: supervisor emits it | FINDING F-9 | no path from child to `needs_approval`; exit 0 = `responded` |
| Reply/receipt wakes nobody, never its author | PASS | `test_a_reply_and_a_receipt_wake_nobody_and_never_their_author` |
| Agent-to-agent task allowed, causally linked | PASS | `test_an_agent_can_task_another_agent_and_the_chain_is_causally_linked` |
| Reply-loop / hop budget | FINDING F-10 | 6 hops dispatched against `max_hops: 3`; `hops_used` never touched |
| Time budget pauses visibly, holds agent, operator cancel frees the queue | PASS | `test_a_run_over_its_time_budget_pauses_visibly_and_holds_the_agent` |
| Cancel is cooperative: ticket kept, job closed, late report refused | PASS | `test_cancel_is_cooperative_retains_the_ticket_and_closes_the_job` |
| Cancel reaches the running child | FINDING F-11 | process keeps running until its own time budget |
| A real spawn completes | FIXED here (F-12) | `test_the_real_launcher_completes_a_run_with_a_real_child_process`; fails at `fe64a8c`, passes at `cb04314` |
| The child is told the task | FINDING F-13 | `test_the_default_prompt_carries_the_task`; live: 266 s, exit 0, never saw the task |
| Healthy idle start <= 5 s (design target) | MEASURED | live spawn 0.004 s (Popen), wall 4.92 s incl. model turn |

Scope note. The master re-clamped T-190 to the happy path (live proof +
isolation + no fan-out) and moved the edge cases to T-404. The edge-case
modules (`test_delivery_guarantees.py`, `test_budgets_and_control.py`) were
already written and green before that note was read; they are on this branch
in their own commits so T-404 can cut from this SHA and start from evidence
rather than re-derive it. Nothing in them is required for the happy-path
verdict.

## Findings, with the fix each needs

Numbered in the order they were found. F-6 is pinned as shipped rather than
xfailed because the design line ("a mention wakes") and the T-187 note ("a
mention is a delivery") disagree and that is a product call, not a defect.

- **F-1 receipts stall at `delivered`.** Nothing transitions a delivery to
  `started`/`responded` or sets `run_id`. Fix: `record_run_event` updates the
  delivery the wake job points at. Lane: T-187 storage.
- **F-2 no reply path.** Child stdout is discarded; child has no board
  credential. Design needs a reply in the source thread. Fix options: the
  supervisor posts the child's final message as an `intent=reply` in the
  source thread under the agent's credential (smallest), or a scoped child
  token. Lane: T-188 + T-187 (needs an agent-credential `/messages` write,
  which exists).
- **F-3 no `runner_offline`.** Dispatch marks `delivered` without checking
  for a live runner lease. Fix: consult `runner_leases` at dispatch. T-187.
- **F-4 no `agent_busy`.** Receipt is written once at dispatch. Same fix
  site as F-3. T-187.
- **F-5 `max_active_tickets=1` = one task ever.** A finished run's ticket
  stays `claimed`, so the next new-ticket task fails `capacity_exhausted`
  instead of queueing. Fix: either the run's terminal event releases/closes
  the draft ticket, or dispatch queues with `agent_busy` until capacity
  returns. Product call on which. T-187/T-188.
- **F-7 revoke does not cancel dispatch.** Fix: `revoke_session_lease` /
  member revoke cancels pending wake jobs and marks deliveries. T-187.
- **F-8 dependency block is a failure, not "waiting".** Fix: dispatch checks
  the linked ticket's dependencies, writes `blocked`/`dependency_unmet` with
  `blocking_ticket_id`, mints no wake job. T-187.
- **F-9 no `needs_approval` from the supervisor.** `claude -p` cannot prompt;
  the supervisor sees only an exit code. Fix needs a signal from the child
  (stream-json output, or a permission-prompt hook) mapped to
  `needs_approval`. T-188. Until then `prompt` policy is a false-green risk.
- **F-10 no hop budget.** `hops_used` is never set or checked. Fix: task
  dispatch walks `causation_id` back to the root and counts agent-authored
  task hops; refuse or queue with a budget reason past `max_hops`. T-187.
- **F-11 cancel does not reach the child.** Supervisor blocks in
  `handle.wait()`. Fix: poll the run (or a cancel long-poll) while waiting
  and `terminate()` on `canceled`. T-188.
- **F-12 every real spawn crashed at `wait()`** -- FIXED on this branch,
  commit `cb04314`, one line in `launcher.py` (`process.stdin = None` after
  the close). Reproduced first with the shell-script child, then live (child
  pid 7426 orphaned and still running after the supervisor crashed). This is
  the T-188 lane's file; the commit is separate so it can be taken or
  replaced by the owner. Without it the runner cannot complete a single real
  run.
- **F-13 the child is never told the task.** `default_prompt` names ids and
  says "read the ticket, report back on the board" to a process with no
  board access. Live, the model searched an empty worktree for 266 s and
  exited 0 -> `responded`. Fix: the wake job payload (or the supervisor via
  `client.get_ticket`) carries the task message body and ticket outcome into
  the prompt. The live test does exactly that through the public
  `prompt_builder` hook. T-188.

## Re-running everything

```
cd /Users/kavana/Downloads/tickets/.worktrees/cursor-demo-t190
TICKET_BOARD_CONTRACTS_REQUIRED=1 python3 -m pytest -q          # whole repo
python3 -m pytest -q tests/acceptance/messaging                  # this suite
python3 -m pytest -q tests/acceptance/messaging --runxfail       # see each finding fail for its stated reason
```

If a finding starts passing, the strict marker fails the suite. Promote it
to a plain test in the same change that fixes it.
