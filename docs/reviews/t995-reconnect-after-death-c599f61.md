# T-995 — reconnect-after-death review

Verdict: **FIX** `c599f6133d539cd1e62f7451dcd881ce62068ba7`.

The source implementation closes the stale-Ready defect without widening a
Ready claim: only an authoritative `unavailable` result for the same host,
user, argv0, environment, repository, origin, and agent may cross binary and
runner-id drift. The candidate does not satisfy the ticket's root/package
parity requirement, however. The installed package CLI has no `harness`
command and cannot execute this path.

## Evidence

- `tests/test_t991_stale_ready.py`: 11 passed at the exact candidate.
- T-991 plus T-685/T-686 focused set: 58 passed, 1 failed. The sole failure is
  the existing custom-watcher process-liveness check; it reproduced in
  isolation on both the candidate and current main with the same empty `ps`
  command-line result.
- The candidate is one commit ahead of current main and merges trivially.
- `python3 tickets.py --help` exposes `harness`; `PYTHONPATH=src python3
  src/ticket_board/cli.py --help` does not.
- The candidate changes root `auth_v2_contract.py`, its contract document, and
  tests. There is no packaged `ticket_board.auth_v2_contract` module and no
  packaged harness-auth implementation.
- `test_packaged_argv0_cannot_overwrite_root_ready` is a pure-function fixture.
  It changes `argv0` in a dictionary; it never launches the packaged CLI.

## Required disposition

Choose and record one release boundary before merging:

1. Add the auth/harness behavior to the packaged CLI and prove the Ready →
   missing-binary transition through both real entry points; or
2. For the macOS developer preview, state that only the source-prefix installer
   is supported, replace the parity requirement with proof that both installed
   `atm` and `tickets` aliases execute the same source script, and keep
   wheel/pipx/Homebrew marked planned.

The candidate is suitable for the supported source path after option 2 is made
explicit and its alias-level transition is tested. It is not package parity as
submitted.
