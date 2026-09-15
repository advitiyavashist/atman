# T-839 — the last missing piece: the legacy-identity test, and what CI was really failing

Branch `atman-identity-boundary-fix-codex-0913` (PR #116). Verified by
`atman-wake-t857-opus-0914` on 2026-09-14 against `origin/main@786be1f`.

The CEO's T-919 verdict was that the code at `236db12` is right and one test is
wrong. That is what this change does, and nothing else. Everything below is the
evidence, including the one CI failure that is *not* ours.

## 1. The one-line contract

`session_seat()` (tickets.py, and the packaged `src/ticket_board/cli.py` in
parity) resolves who a session is as:

    explicit argument > TICKET_SEAT > a SESSION-KEYED recorded identity
        > TICKET_AGENT > the flat legacy .agent-identity file > pid

The flat file is shared by every agent on the machine, so it ranks below
anything that actually names the caller. `tests/test_identity_session_scope.py`
asserted the opposite for the no-session-key case (`"ambient" == "legacy-name"`),
which is the assertion that would reintroduce the regression the CEO reported:
`TICKET_AGENT=master tickets msg --to worker`, run after the worker joined,
resolves the master AS the worker and refuses its own message as self-addressed.

The test now asserts the real contract:

* nothing names the caller → the flat file answers (`seat_of(board)`);
* `TICKET_AGENT` set → the explicit name wins, no session key needed;
* `TICKET_SEAT` set → same;
* a keyed session → it does not adopt the file either (unchanged).

**Negative proof.** Swapping just the `env_agent` / `recorded` order inside
`session_seat` — no test edits — fails the test with `- ambient / + legacy-name`.
The test gates the regression rather than describing the code.

## 2. Why the branch also merges current main

`236db12` had exactly two CI failures that main did not have:

| failure | cause |
| --- | --- |
| `test_identity_session_scope.py::test_legacy_flat_file_is_honoured_only_when_there_is_no_session_key` | the wrong assertion above — fixed here |
| `test_contracts.py::test_no_test_file_names_a_real_operator_home` | staleness, not this branch |

The second one needs saying plainly: the branch was based on `3679053`, whose
`tests/test_t790_ceo_onboarding.py` still spelled out a real operator home.
Main scrubbed those literals to `$HOME` after `3679053`. The diff
`3679053..236db12` for that file is empty — the branch never touched it, it was
simply behind. Merging `786be1f` clears it, and it is the only way to compare
this branch against the main it would land on.

## 3. CI failure sets: this head against the main it would land on

Extracted from the same workflow's logs (`gh run view --log-failed`), comparing
the exact PR head against the exact main it merges:

| head | CI run | failures |
| --- | --- | --- |
| `main@786be1f` | [34805162922](https://github.com/advitiyavashist/atman/actions/runs/34805162922) | 29 |
| PR #116 `@3473ae3` | [34807208908](https://github.com/advitiyavashist/atman/actions/runs/34807208908) | 29 |
| PR #116 `@3473ae3` (same SHA, second run) | [34807210946](https://github.com/advitiyavashist/atman/actions/runs/34807210946) | 31 |

**Run 34807208908 has a failure set identical to main's, element for element:
zero new, zero fixed.** That is the number the CEO asked for.

The second run is at the *same commit* and adds two:

| extra failure in run ...946 only | |
| --- | --- |
| `test_t611_objective_bounds.py::test_spawn_cos_defaults_persistent_with_max_runs_oneshot` | flake |
| `test_t889_work_view.py::test_reopen_stamps_reopened_at_so_old_posts_drop_out` | flake, and pre-existing — see §4 |

Two runs of one commit cannot differ because of the commit. Same-SHA divergence
is the cleanest available proof that both are CI flakes rather than regressions.

The 29 shared failures are the `ps`/process-visibility and sandbox classes that
main already carries: t278, t409, t422, t427, t554, t559, t611, t778, t784,
t785, t808, trajectories, ui_server_harness.

## 4. The t889 failure is pre-existing, and not ours

Four independent facts:

1. **It fails without this branch.** Run
   [34759945333](https://github.com/advitiyavashist/atman/actions/runs/34759945333),
   on T-889's own branch `atman-ui-work-fable-0913@8308f5c`, failed this exact
   test on 2026-09-13 — a day before this branch existed.
2. **It is not deterministic here either.** It failed in only one of the two
   CI runs of `3473ae3` (§3) — same commit, different answer.
3. **The mechanism is visible in the code.** `work_view.py` decides "belongs to
   the ticket's previous life" with `(m.get("at") or "") < epoch`, on timestamps
   that `now()` renders at one-second resolution. A task post and a reopen
   inside the same second are therefore indistinguishable from a post *after*
   the reopen.
4. **It reproduces at the unit level, identically on both trees.** `work_view.py`
   is byte-identical on `786be1f` and on this branch, and this pure-payload
   probe prints the same two lines in both:

   ```python
   # post 1s BEFORE reopen -> ('ready', 1, True)      dispatch dropped, 1 stale
   # post in the SAME second -> ('posted', 0, False)  treated as current
   from ticket_board import work_view
   t = {"id": "T-001", "title": "Write docs", "status": "open", "role": "docs",
        "reopened_at": REOPENED, "deps": []}
   msgs = [{"id": "m1", "kind": "task", "re": "T-001", "to": "bob",
            "from": "planner", "at": POST_AT, "text": "take T-001"}]
   work_view.work_payload([t], {"nodes": [{"id": "T-001"}], "edges": [],
                                "roots": ["T-001"]}, msgs,
                          objective={"text": "o"}, agents={"bob": {}})
   ```

   `('posted', 0, False)` is exactly the CI assertion failure
   (`assert 'posted' == 'ready'`).

Locally the test passes 8/8 in isolation and 21/21 as a file, on this branch and
on a clean `786be1f` tree, in the hermetic env — macOS subprocess latency puts
the post and the reopen in different seconds. A fast Linux runner does not.
That is a T-889 defect (its staleness comparison needs sub-second or sequenced
stamps, or the test needs to stamp distinct times); it is not fixed here because
it is not this ticket's code, and reporting it as ours would be wrong.

## 5. Hermetic full suite

`docs/verification/hermetic-preflight.md` conditions: CPython 3.9.6, pinned pytest 8.4.2,
`env -i`, throwaway `HOME`/`TMPDIR` per run, `PYTHONNOUSERSITE=1`, no
`PYTHONPATH`, `-p no:cacheprovider`, detached worktrees, never the live board.

Two matched runs, candidate and baseline, under those conditions:

| tree | result |
| --- | --- |
| `main@786be1f` | **55 failed**, 2126 passed, 24 skipped, 11 xfailed (27:59) |
| PR #116 `@3473ae3` (tree-identical `45aad9e`) | **12 failed**, 2200 passed, 24 skipped, 11 xfailed (30:02) |

Set difference, candidate minus baseline — **one** entry:

    tests/test_t611_objective_bounds.py::test_watch_persist_loops_until_sigterm

It is a load flake, not a regression. Both suites ran while the rest of the
board was working the machine, and the assertion is a liveness check
(`assert proc.poll() is None, "persist watcher must still be running"` at
`tests/test_t611_objective_bounds.py:345`) — the watcher had already exited 0.
Re-run in the same hermetic env it passes **5/5 in isolation and 3/3 as a whole
file, on this branch and on a clean `786be1f` tree alike**. CI failed it on
neither run of this head.

The 44 failures that are on main but *not* on this branch are almost entirely
the `[cli.py]` parametrisations (t263, t273, t394, t409, t424, t428, t494, t503,
t552, t781, t804, safe_clear, trajectories_entrypoints). Those are the
plain-source packaged-CLI import path this branch already repaired; the branch
fixes them, it does not skip them. The suite totals differ (2247 vs 2216
collected) because this branch adds the identity and boundary test files.


## 6. What was NOT done, deliberately

* No product code changed. `tickets.py` and `src/ticket_board/cli.py` carry the
  CEO-approved `236db12` behavior untouched.
* No T-889 fix, no T-828/T-861 work mixed in.
* No live-board interaction of any kind.
