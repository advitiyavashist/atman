# T-876 — independent verification of PR #116 (T-839 identity boundary)

Verifier: `atman-verify-t876-opus-0914` (non-author). Candidate: **f3289b2**
(`f3289b25eddf5690634aa66a7a1c876ed2a5e579`). Baseline: **main @ 9b2057d**.

`git merge-base main f3289b2` = `9b2057d`, so the candidate is main plus the PR
delta — no stale-base artifacts. Head composition: `f3289b2` = `3473ae3`
(the reviewed code) + an evidence doc + a main sync that is README-only.

## Verdict: FIX

The delta does what it claims for the cases it tests, and the full clean-env
suite shows no new failures. It is nevertheless **not mergeable as-is**: the
PR introduces a new, reproducible way for a session to act as — and silently
consume the private mail of — a seat the board explicitly **refused** it.
That is the exact failure class T-839 exists to close, and no test in the PR
covers it, so CI cannot see it.

## Blocking finding — a REFUSED `tickets join` still claims the seat

`cmd_join` records this session's identity at `tickets.py:8892`
(`write_identity(board, owner)`) — **before** any of the checks that can reject
the join. Every `sys.exit()` after that line therefore leaves the session
already stamped `.identities/<session-key> -> <the refused name>`:

- the four `--knowledge-dir` validations (inside the board, no `manifest.json`,
  invalid graph) — `tickets.py:8894-8903`
- `_guard_seat_identity` — harness-reuse conflict, alias clash, `--transfer`
  while holding a ticket — `tickets.py:8905`
- `--harness custom` with no `--cmd` — `tickets.py:8938`

`cli.py` has only the guard after its write (`:4076` → `:4078`), so the exposure
is narrower there but the same shape. The two checks that run *before* the
stamp (`_refuse_join_tickets_dir_shadow`, the `agent-` name refusal) are the
ones that behave correctly, which is the pattern the rest should follow.

Because `session_seat()` ranks a **session-keyed recorded join above
TICKET_AGENT** (correctly — that is the fix's core precedence), the stamp then
outranks the agent's real identity for every seat-scoped surface: `msg`'s
sender, `inbox`'s owner, `board`'s "you:" line, the stop-hook.

### Reproduction (two-sided, disposable boards only)

`docs/reviews/t876/refused_join_identity.sh <tree>` — exits 1 on the leak,
0 when clean. Session B holds `TICKET_AGENT=bravo` and is refused
`join alpha --tool codex` (alpha is bound to claude).

| tree | after the refusal, session B's bare `inbox` | bare `msg` posts as |
|---|---|---|
| candidate f3289b2 | `2 unread for alpha`, **including carol's private DM to alpha — and marks it read** | `alpha` |
| main 9b2057d | `2 unread for bravo` | `bravo` |

Two distinct harms, not one: impersonation (B posts as alpha), and mail
**loss** — B consumes alpha's unread DM, so the real alpha never sees it and
nothing indicates it was delivered elsewhere.

Reachability is not exotic. The refusal message itself tells the agent to
retry under a different name ("Join a unique provider-specific agent id"), so
the window between a refused join and a successful re-join is the normal path
through this error, and any `inbox`/`msg` in that window acts as the refused
seat.

### Proposed fix (validated here, not applied — non-author)

Move the `write_identity(board, owner)` call to **after**
`_guard_seat_identity(...)` (and after the `--knowledge-dir` validation), in
both `tickets.py` and `src/ticket_board/cli.py`. The author's stated intent —
"joining IS the declaration of who you are" (`tickets.py:8884-8891`) — is
preserved; a join the board *rejects* is simply not a declaration it accepted.

Checked both directions against a patched copy:

- refused join → session B stays `bravo`, sees only bravo's mail (leak closed)
- accepted join → `.identities/<key> -> realseat`, and a later bare `msg` in
  that session posts as `realseat` even though `TICKET_AGENT=envname`
  (the keyed-join-beats-ambient-env precedence still works)

A regression test belongs with it: refuse a join, then assert the session's
seat is unchanged. There is currently no test that exercises any refusal path
in `cmd_join`.

## Full clean-env suite comparison

Per the CEO's standing rule after the overturned `2a942f0` ACCEPT: FULL suite,
clean environment, candidate vs main at the same base, new failures must be 0.

Method — two disposable clones, each with its own `uv` py3.11 venv, CI parity
(`pip install -e .[contracts]`, `TICKET_BOARD_CONTRACTS_REQUIRED=1`,
`python -m pytest -q`), each run under `env -i` with a per-tree `HOME` and
`TMPDIR` and `-p no:cacheprovider`. Run **sequentially**, never in parallel:
several known-flaky tests here are timing-sensitive and CPU contention alone
flips them. The live board was never used.

**Result: 0 new failures.** Every difference between the two lists is an
artifact of my own harness or a flake, and each one is shown to be so below
rather than waved away.

| | candidate f3289b2 | base 9b2057d |
|---|---|---|
| summary | 11 failed, 2196 passed, 26 skipped, 11 xfailed, **3 errors** in 1599s | 10 failed, 2170 passed, 25 skipped, 11 xfailed in 1555s |

Ten failures are **identical on both trees** and therefore pre-existing on main:
`t257_board_guard::test_refuses_a_board_outside_tmp_while_under_pytest`,
four `t278_agent_record_race` straddling-heartbeat cases,
`t611_objective_bounds::test_review_tasks_cos_once_then_inbox_clears_repeat_wake`,
`t707_docker_packaging::test_wheel_installs_tickets_console_script`,
`t778_master_onboarding`, `t784_team_intro`, and
`trajectories::test_claim_update_review_done_are_all_recorded`.

Four entries appeared on the candidate only. All four are resolved, not assumed:

**1. `t683_session_adapters::test_wake_delivery_is_deduped_by_message_id` — flake.**
I had earlier guessed this was T-857's merged receipt change in main; the base
run disproved that (base passes it), so I chased it properly instead of keeping
a convenient story. `session_adapters.py` is **byte-identical** between the two
trees (`git diff 9b2057d f3289b2 -- session_adapters.py` is empty), and the test
loads that file directly by path via `importlib.spec_from_file_location`, so no
line the PR changes is executed by it. Re-run **5x on each tree** under the same
recipe: **10/10 passed**. The assertion is `wake_seat(...) == "delivered-unconfirmed"`
but got `"woken"` — the receipt/ack timing path, over a socket in shared `/tmp`
(`sock_dir = /tmp/t683...`, outside the isolated `TMPDIR`), so another agent's
pytest on this machine is enough to flip it.

**2-4. Three `t839_boundary_adapters` ERRORs — a hole in my harness, now closed.**
These error at *setup*, not in the assertion: the test shells out to `pip install`
the tree, and my `uv` venvs had no `setuptools`, so the build backend was missing
(`BackendUnavailable: Cannot import 'setuptools.build_meta'`). This is the PR's own
new test file, so base has no counterpart to cancel it out — the comparison could
not see it. I seeded `setuptools`/`wheel`/`build` identically into **both** venvs
and re-ran: **`tests/test_t839_boundary_adapters.py` 5 passed** on the candidate.
The same seeding also turns the shared `t707_docker_packaging` failure green on
**both** trees (9 passed each), confirming that one as an env artifact of my venv
rather than a real main-side defect.

Net, after attributing every difference: **new failures = 0**, and the PR's own
boundary tests genuinely run and pass.

## CI parity (re-derived independently, not taken from the author)

I pulled the raw GitHub logs myself and diffed the FAILED sets; nothing here is
taken from the author's summary. Extraction was identical for every run
(`gh run view <id> --log`, `grep -oE 'FAILED tests/[^ ]+'`, `sort -u`).

| run | SHA | tree | FAILED |
|---|---|---|---|
| `34805162922` | `786be1f` | main | 29 |
| `34807208908` | `3473ae3` | PR #116 | 29 — **set identical to main, element-for-element** |
| `34807210946` | `3473ae3` | PR #116, *same SHA re-run* | 31 |
| `34811195620` | `f3289b2` | PR #116 head (fresh, finished during this session) | 30 |
| `34759945333` | `8308f5c` | `atman-ui-work-fable-0913`, 09-13 | 31 |

main `786be1f` vs current main `9b2057d` differ by README only (22 lines), so
that run is a valid baseline for today's main.

**Fresh run on the actual head `f3289b2`: 30 failures = main's 29 plus exactly
one test**, `test_t889_work_view::test_reopen_stamps_reopened_at_so_old_posts_drop_out`.
Nothing on main's list disappeared. That one test is not this PR's, and I can
show the mechanism rather than just assert flake:

- **The PR cannot reach it.** `git diff --name-only 9b2057d..f3289b2` touches
  neither `tests/test_t889_work_view.py` nor `src/ticket_board/work_view.py`.
- **It predates this branch.** It failed on T-889's *own* branch a day earlier
  (run `34759945333`, `atman-ui-work-fable-0913@8308f5c`, 09-13).
- **Two runs of one commit disagree.** `3473ae3` failed it in `34807210946`
  and passed it in `34807208908` — flake by construction.
- **Root cause is a same-second timestamp race in T-889's code.** `now()`
  (`tickets.py:846`) is second-granular (`%Y-%m-%dT%H:%M:%SZ`), and
  `work_view._dispatch_of` drops a stale post with a *strict* compare,
  `if epoch and (m.get("at") or "") < epoch` (`work_view.py:289`). The test
  posts a task and then reopens the ticket; on a fast runner both land in the
  same wall-clock second, so `m["at"] == epoch`, the post is not ignored, and
  `phase` stays `"posted"` where the test asserts `"ready"` — which is exactly
  the observed `AssertionError: assert ('posted' == 'ready'`. Worth its own
  ticket against T-889 (`<=` plus a sub-second or monotonic epoch); it is out
  of scope here.

So CI parity holds: **zero new failures attributable to PR #116**, and zero of
main's failures fixed by it.

## Negative control

The PR's tests were kept and the PR's **production** code reverted to main.
A new test that still passes with the fix removed proves nothing about the fix.

`tickets.py`, `src/ticket_board/cli.py` and `src/ticket_board/agent_checkin.py`
were restored to `9b2057d` in a copy of the candidate tree. The PR's new
`session_boundary.py` module was **kept** so the test imports still resolve —
my first attempt reverted it too, which only produced a collection `ImportError`
and proved nothing; that run is discarded.

Result: **18 failed, 8 passed** — the PR's behavioural tests genuinely fail
without the PR's production code, across all three modules
(`test_identity_precedence.py` 6 failures, `test_identity_session_scope.py` 7,
`test_t839_launch_identity.py` 5), including
`test_join_records_identity_and_it_outranks_the_env_var` and
`test_recorded_identity_does_not_bleed_to_another_session`. The fix is
load-bearing and its tests are real.

Separately, the **regression test for the blocking finding** was held to the same
standard: `test_a_refused_join_does_not_claim_the_seat` and
`test_a_refused_join_does_not_consume_the_refused_seats_mail` **FAIL on f3289b2
as-is** (`assert 'alpha' == 'bravo'`; `inbox` prints `2 unread for alpha`
including carol's private DM) and **PASS** once `write_identity` moves below the
guards. On that same patched tree the identity modules the PR exists to
establish still pass in full — `test_identity_precedence.py`,
`test_identity_session_scope.py`, `test_t839_launch_identity.py`,
`test_byoa.py`, `test_t327_join_inbox_watermark.py`: **74 passed** — so the
proposed fix closes the leak without weakening the precedence it protects.

## T-847 / T-844 reproducers and wheel smoke

**T-847 prior comparison** (`repro/prior_comparison_t876.py`, exit 0) —
reproduces the pre-fix leaks on `feca2f5` and confirms the candidate closes them:

| | `prior-feca2f5` | `candidate-f3289b2` |
|---|---|---|
| remote parent leak | `True` | `False` |
| worker mail | `False` | `True` |
| sender | `parent-seat` | `worker-seat` |
| parent Claude hook survives | `True` | `False` |
| parent bytes unchanged | `True` | `True` |

**T-844 root/wheel/spawn probe** (`repro/t844_probe_t876.py`, exit 0) — all cases
PASS in **both** root and installed-wheel modes: `EXPLICIT_SEAT_OVERRIDES_FORGED_SID`,
`GENUINE_RECORDED_SESSION_PRECEDENCE`, `ROOT_GENERATED_WORKER_HOOK_IDENTITY`,
`ROOT_REAL_SPAWN_WORKTREE_REGISTRATION`, and
`CLAUDE_SETTINGS_LOCAL_CANONICAL_HOOK_SURVIVES_WORKER_PIN`.
This probe needs the installed console script, so it could not run until the
wheel below actually built — an earlier "rc=1" for it was that missing wheel,
not a candidate failure.

**Clean-venv wheel smoke** — `python -m build --wheel` produces
`ticket_board-0.2.0-py3-none-any.whl`; installed into a fresh `uv` py3.11 venv it
imports from `site-packages` (`.../wheel/venv/lib/python3.11/site-packages/ticket_board/__init__.py`),
not the source tree. End-to-end under `env -i` on a disposable board:
`tickets init` → `join smoke --tool claude` → `who` → `msg --to smoke` → `inbox`
all succeed. (`tickets --version` exits 2 only because this CLI has no
`--version` flag — my probe was wrong, not the wheel.)

## Static review notes (non-blocking)

- `cmd_note` now attributes via `whoami(a.by)` while `msg`/`inbox` use
  `session_seat(...)`. The two diverge only when a session's keyed join
  differs from its `TICKET_AGENT`; `session_seat` looks like the intended
  resolver for a note's author. Worth a deliberate decision either way.
- `traj_event(board, "update", agent=who or whoami(), ...)` in `cmd_note`:
  `who` is now `whoami(a.by)`, which never returns falsy, so `or whoami()` is
  dead.
- `_inherit_settings` no longer carries `.agents/hooks.json`, so the tuple's
  `prefixed` flag is always `False` and
  `os.path.join(dname, name) if prefixed else name` is dead.
- `cmd_hooks` replaced the local `ours()` filter with
  `session_boundary.without_board_hooks(s)`, which widens the scrub from the
  three events being rewritten to **every** hook event. Board hooks a user
  installed under, say, `PostToolUse` are now stripped and not re-added.
  Plausibly intended; it is a scope change worth stating in the PR.
- `adopt_event_session` trusts `event["session_id"]` from the hook payload.
  Reasonable — that is the only channel that can know it, and the value is
  hashed before use, so no path traversal. Impersonation would require
  already-local execution plus the raw session id.
- Boundary/precedence parity between `tickets.py:session_seat` and
  `cli.py:session_seat` confirmed identical, including the CEO's requirement
  that an explicit `TICKET_AGENT` beats the flat legacy identity file.

## Scope discipline

Disposable boards only; no live-board testing, no live-model wake, no
canonical seat edits, no implementation edits to the PR, no merge. Merge
authority stays with the CEO.
