# Merge train 0914 -- integration verification (T-919)

Verdict: **FIX -- PR #116**. New failures vs `main` = **1**. Every other
failure ID is identical on both sides.

## What was compared

| | base | candidate |
| --- | --- | --- |
| SHA | `403c47d` (origin/main) | `9f063dd` (`ceo/merge-train-0914`) |
| result | 10 failed, 2126 passed, 24 skipped, 11 xfailed (1:00:47) | 11 failed, 2201 passed, 24 skipped, 11 xfailed (1:03:45) |

Candidate = base + five `--no-ff` merges, verified by first-parent walk of
`403c47d..9f063dd`. Second parents, in order, each matching the live PR head:

| merge order | second parent | PR |
| --- | --- | --- |
| 1 | `d1c9f3e` | #137 T-857 wake receipts |
| 2 | `236db12` | #116 T-839 session-scoped identity |
| 3 | `26ff79f` | #129 T-809 `atm` primary CLI |
| 4 | `84e47ac` | #121 T-879 plan-ready cause/change/proof |
| 5 | `8308f5c` | #127 T-889 Work view |

Both suites ran in the same recorded environment, built by
`scripts/hermetic_preflight.py` (see `hermetic-preflight.md`), which returned
`PASS` for both origins: Python 3.9.6, `pytest==8.4.2`, `PYTHONPATH` unset,
`PYTHONNOUSERSITE=1`, `HOME`/`TMPDIR` sanitized into the workdir, no operator
`TICKET_*`/`CLAUDE_*`/`CURSOR_*`/`CODEX_*` values inherited. The wheel built
from the candidate exposes both `atm` and `tickets` consoles sharing one entry
point (`atm_same_entry: true`); the base wheel exposes `tickets` only.
Counts were never the verdict -- the comparison is over `-rf` failure-ID sets.

## Failure-ID diff

Only on base (i.e. fixed or masked by the train): **none**.

On both (pre-existing on `main`, out of scope for this train):

- `tests/test_t257_board_guard.py::test_refuses_a_board_outside_tmp_while_under_pytest`
- `tests/test_t278_agent_record_race.py::test_limit_survives_a_heartbeat_that_straddles_it`
- `tests/test_t278_agent_record_race.py::test_clearing_a_limit_survives_a_straddling_heartbeat`
- `tests/test_t278_agent_record_race.py::test_inbox_seen_survives_a_straddling_heartbeat`
- `tests/test_t278_agent_record_race.py::test_stop_blocks_survive_a_straddling_heartbeat`
- `tests/test_t611_objective_bounds.py::test_review_tasks_cos_once_then_inbox_clears_repeat_wake`
- `tests/test_t778_master_onboarding.py::test_master_template_and_howto_start_with_onboarding`
- `tests/test_t784_team_intro.py::test_howto_still_starts_with_onboarding`
- `tests/test_t882_install_prefix.py::test_refuses_to_clobber_a_live_release_launcher_without_force`
- `tests/test_trajectories.py::test_claim_update_review_done_are_all_recorded`

Only on candidate -- **the one new failure**:

```
tests/test_identity_session_scope.py::test_legacy_flat_file_is_honoured_only_when_there_is_no_session_key
tests/test_identity_session_scope.py:123: AssertionError: assert 'ambient' == 'legacy-name'
```

## Attribution: PR #116, on its own

1. `tests/test_identity_session_scope.py` is present on `236db12` and on no
   other head in the train, and absent from `403c47d`.
2. `main` + #116 alone -- `403c47d` + `--no-ff` `236db12`, commit `6364f42`,
   hermetic preflight `PASS` -- fails identically. Not a train interaction.
3. Running the file by itself on `9f063dd` fails identically (1 failed,
   7 passed). Deterministic, not test-ordering pollution.

The contradiction is internal to #116. `session_seat()` in `tickets.py`
documents *and implements* the precedence

```
explicit > TICKET_SEAT > session-keyed recorded identity > TICKET_AGENT
        > flat legacy .agent-identity > pid
```

while the test #116 adds asserts the flat legacy file outranks an ambient
`TICKET_AGENT`. One of the two halves is wrong and choosing which is the
author's call. The docstring argues for the code: putting the flat file above
`TICKET_AGENT` makes `TICKET_AGENT=master tickets msg --to worker` resolve the
master as the worker after the worker has joined, and the message is then
refused as self-addressed.

## Other required checks (all green, candidate unless noted)

- Wake frames, run standalone and isolated: `test_t857_claude_uds`,
  `test_t857_codex_ws`, `test_t857_reachability`, `test_schedule_wake`,
  `test_t640_continuous_wake`, `test_t706_pause_wake`,
  `test_t785_t789_all_provider_wake`, `test_wakeup` -- 107 passed, 0 failed.
- T-847 reproducer: the remote parent-seat leak reproduces on `feca2f5`
  (`remote_parent_leak=True`, sender `parent-seat`) and is clean on `9f063dd`
  (`leak=False`, `worker_mail=True`, sender `worker-seat`).
- T-844 probe: all PASS on the candidate source tree *and* through the wheel
  console -- explicit-seat override, recorded-session precedence, worker hook
  identity, real spawn worktree registration, remote wrapper seat inheritance
  against baseline `3679053`, `claude` `settings.local` pin.
- Clean-venv wheel identity: base exposes only `tickets`; candidate exposes
  `atm` and `tickets`, help identical modulo program name, and directed mail
  stays seat-scoped through either entry point.
- README first win A->B on a throwaway board: `install.sh --prefix`,
  quickstart, objective, A creates `T-004` plus directed mail, B joins,
  B's inbox shows only B's mail, B claims via `atm next`, board shows
  `owner=bob`.

## Recommendation

#137, #129, #121 and #127 are untouched by this failure and can merge at their
exact heads. Hold #116 until the test/precedence contradiction is resolved;
re-verifying #116 alone against `main` is then enough, since the train
introduces no cross-PR interaction.
