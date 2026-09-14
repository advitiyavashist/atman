# T-876 — independent verification of PR #116 (T-839 identity boundary)

Verifier: `atman-verify-t876-opus-0914` (non-author).
**Candidate: `d70b55e`** (`d70b55ed83c4329d6a40aab5f940e7b13a8fe174`).
Baseline: **`9b2057d`** — `git merge-base main d70b55e`, so the diff isolates
the PR delta. (main has since moved to `7795cef`; the merge-base, not the moving
tip, is the baseline that attributes failures to *this* PR.)

## Verdict: ACCEPT `d70b55e`

**New failures vs baseline: 0.** The full clean-env suites produce FAILED sets
that are identical element-for-element, with zero errors on either side:

| | candidate `d70b55e` | base `9b2057d` |
|---|---|---|
| | 9 failed, **2215 passed**, 24 skipped, 11 xfailed, 0 errors (1757s) | 9 failed, **2172 passed**, 24 skipped, 11 xfailed, 0 errors (1631s) |

New on candidate: **none**. Fixed by candidate: none. The nine shared failures
are pre-existing on main: `t257_board_guard`, four `t278_agent_record_race`
straddling-heartbeat cases, `t611_objective_bounds`, `t778_master_onboarding`,
`t784_team_intro`, `trajectories`.

### History of this verdict (recorded, not hidden)

This ticket's earlier SHA `f3289b2` was headed for **FIX**: I found and
reproduced a blocking privilege-escalation regression in it (below). While my
suites were running the author pushed `d70b55e`, which applies exactly the fix
I had proposed and validated, plus 202 lines of tests pinning it. I re-verified
the new head from scratch rather than reporting a verdict on a dead SHA. The
finding is kept here because it is the substance of this verification and
because the fix must be checked, not assumed.

## The blocking finding (found at `f3289b2`, fixed at `d70b55e`)

At `f3289b2` the PR introduced a reproducible way for a session to act as — and
silently consume the private mail of — a seat the board explicitly **refused**
it. That is the exact failure class T-839 exists to close, and no test in the
PR covered it, so CI could not see it.

### The defect, as it stood at `f3289b2`

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

### Proposed fix (validated here; the author then applied it)

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

## Verification of the fix at `d70b55e`

The delta `f3289b2..d70b55e` is 3 files: `tickets.py` (+24/-9),
`src/ticket_board/cli.py` (+7/-2), and a new
`tests/test_t839_refused_join_identity.py` (202 lines). It is exactly the
change proposed above — `write_identity(board, owner)` moved below every guard
(`tickets.py:8985`, `cli.py:4086`) — with the reasoning written into the code.

**The new comment's load-bearing claim is checked, not trusted.** It asserts
"Nothing below this line can sys.exit." I verified that structurally with an
AST walk of `cmd_join` in both files rather than by reading: in `tickets.py`
all **8** `sys.exit` calls precede the stamp and none follow it; in `cli.py`
the **1** exit precedes it. No `raise` follows it either. So every refusal path
— the four `--knowledge-dir` validations, `_guard_seat_identity`, the
`--harness custom` typo guard, the lifecycle/persistence flag checks — now
returns before the session is ever named.

**Behavioural checks on `d70b55e`:**

- My own two reproducer tests, written against `f3289b2` *before* the author's
  fix existed and unchanged since, now **pass**.
- The author's new suite: **12 passed**, covering provider-reuse, alias-clash,
  `--knowledge-dir`, and late-flag refusals across **both** entry points
  (`root` and installed `wheel`).
- Identity/precedence/session-scope/launch/boundary-adapters/BYOA/t327:
  **79 passed**.

**Negative control on the new tests** — the tests are load-bearing, not
decorative. Keeping `d70b55e`'s tests and reverting **only** the two production
files to `f3289b2`'s ordering: **10 of 12 fail**, including
`test_refused_session_cannot_read_or_post_as_the_seat_it_was_refused[root]` and
`[wheel]` and all four `test_refused_join_leaves_the_session_identity_untouched`
cases, each with the original signature `AssertionError: assert 'alpha' == 'bravo'`.
The two that still pass are the ones whose refusal happens before the old stamp
site, which is the correct result.

## Full clean-env suite comparison

Per the CEO's standing rule after the overturned `2a942f0` ACCEPT: FULL suite,
clean environment, candidate vs main at the same base, new failures must be 0.

Method — two disposable clones, each with its own `uv` py3.11 venv, CI parity
(`pip install -e .[contracts]`, `TICKET_BOARD_CONTRACTS_REQUIRED=1`,
`python -m pytest -q`), each run under `env -i` with a per-tree `HOME` and
`TMPDIR` and `-p no:cacheprovider`. Run **sequentially**, never in parallel:
several known-flaky tests here are timing-sensitive and CPU contention alone
flips them (another agent's pytest was live on this machine throughout). The
live board was never used.

### Final round — `d70b55e` vs `9b2057d`

| | candidate `d70b55e` | base `9b2057d` |
|---|---|---|
| | 9 failed, 2215 passed, 24 skipped, 11 xfailed, **0 errors** (1757s) | 9 failed, 2172 passed, 24 skipped, 11 xfailed, **0 errors** (1631s) |

**NEW FAILURES = 0.** The two FAILED sets are identical element-for-element;
nothing on base's list disappeared either. The +43 passing tests on the
candidate are the PR's own additions. This round ran with the build backend
seeded identically in both venvs, so the artifacts described below are gone
from the comparison rather than explained away.

### First round — `f3289b2` vs `9b2057d`, and the two harness holes it exposed

Recorded because the reasoning matters and because one of the differences was
my own harness, not the code. Candidate `f3289b2`: 11 failed / 2196 passed /
**3 errors**; base `9b2057d`: 10 failed / 2170 passed. Ten failures matched
exactly. Four entries were candidate-only, and all four were resolved rather
than assumed:

**1. `t683_session_adapters::test_wake_delivery_is_deduped_by_message_id` — flake.**
I had earlier guessed this was T-857's merged receipt change in main; the base
run disproved that (base passes it), so I chased it properly instead of keeping
a convenient story. `session_adapters.py` is **byte-identical** between the two
trees (`git diff 9b2057d f3289b2 -- session_adapters.py` is empty), and the test
loads that file directly by path via `importlib.spec_from_file_location`, so no
line the PR changes is executed by it. Re-run **5x on each tree**: **10/10
passed**. The assertion wants `"delivered-unconfirmed"` but got `"woken"` — the
receipt/ack timing path, over a socket in shared `/tmp` (`sock_dir = /tmp/t683…`,
outside the isolated `TMPDIR`), so another agent's pytest is enough to flip it.
CI later confirmed this independently: see the two same-SHA `d70b55e` runs below.

**2-4. Three `t839_boundary_adapters` ERRORs — a hole in my harness, now closed.**
These errored at *setup*, not in an assertion: the test shells out to `pip
install` the tree, and my `uv` venvs had no `setuptools`, so the build backend
was missing (`BackendUnavailable: Cannot import 'setuptools.build_meta'`). This
is the PR's own new test file, so base had no counterpart to cancel it out — the
comparison could not see it, and the PR's boundary tests were silently not
running. I seeded `setuptools`/`wheel`/`build` identically into **both** venvs
and re-ran: **5 passed**. The same seeding turns the shared `t707_docker_packaging`
failure green on **both** trees (9 passed each), so that one was an artifact of
my venv rather than a real main-side defect — which is why the final round shows
nine shared failures, not ten.

## CI parity (re-derived independently, not taken from the author)

I pulled the raw GitHub logs myself and diffed the FAILED sets; nothing here is
taken from the author's summary. Extraction was identical for every run
(`gh run view <id> --log`, `grep -oE 'FAILED tests/[^ ]+'`, `sort -u`).

| run | SHA | tree | FAILED |
|---|---|---|---|
| `34805162922` | `786be1f` | main | 29 |
| `34813485141` | **`d70b55e`** | **PR #116 head** | **29 — set IDENTICAL to main, element-for-element** |
| `34813482167` | **`d70b55e`** | PR #116 head, *same SHA re-run* | 30 |
| `34807208908` | `3473ae3` | PR #116, earlier | 29 — also identical to main |
| `34807210946` | `3473ae3` | PR #116, *same SHA re-run* | 31 |
| `34811195620` | `f3289b2` | PR #116, earlier head | 30 |
| `34759945333` | `8308f5c` | `atman-ui-work-fable-0913`, 09-13 | 31 |

main `786be1f` vs current main `7795cef` differ by README-scale changes only, so
that run remains a valid baseline.

**On the actual head `d70b55e`, one CI run's failure set is identical to main's
element-for-element — zero new, zero fixed.** The second run of that same commit
differs by exactly one test:
`test_t683_session_adapters::test_wake_delivery_is_deduped_by_message_id`.

That is the strongest possible confirmation of the flake call I made locally,
and it is not my evidence: **two CI runs of one identical commit disagree on
that single test, on hardware that is not mine.** It is the same test my local
re-runs cleared 10/10, and the PR cannot reach it — `session_adapters.py` is
byte-identical across the delta.

The earlier `f3289b2` run's extra failure,
`test_t889_work_view::test_reopen_stamps_reopened_at_so_old_posts_drop_out`, was
likewise not this PR's, and I can show the mechanism rather than assert flake:

- **The PR cannot reach it.** The delta touches neither
  `tests/test_t889_work_view.py` nor `src/ticket_board/work_view.py`.
- **It predates this branch.** It failed on T-889's *own* branch a day earlier
  (run `34759945333`, `atman-ui-work-fable-0913@8308f5c`, 09-13).
- **Root cause is a same-second timestamp race in T-889's code.** `now()`
  (`tickets.py:846`) is second-granular (`%Y-%m-%dT%H:%M:%SZ`), and
  `work_view._dispatch_of` drops a stale post with a *strict* compare,
  `if epoch and (m.get("at") or "") < epoch` (`work_view.py:289`). The test
  posts a task then reopens the ticket; on a fast runner both land in the same
  wall-clock second, so `m["at"] == epoch`, the post is not ignored, and `phase`
  stays `"posted"` where the test asserts `"ready"` — exactly the observed
  `AssertionError: assert ('posted' == 'ready'`. Worth its own ticket against
  T-889 (`<=` plus a sub-second or monotonic epoch); out of scope here.

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

Re-checked against `d70b55e`, not carried over from the earlier SHA. One
earlier note is dropped: the dead `or whoami()` in `cmd_note`'s `traj_event`
call is gone at this head (`tickets.py:5379` now passes `agent=who`).

- `cmd_note` now attributes via `whoami(a.by)` while `msg`/`inbox` use
  `session_seat(...)`. The two diverge only when a session's keyed join
  differs from its `TICKET_AGENT`; `session_seat` looks like the intended
  resolver for a note's author. Worth a deliberate decision either way.
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
