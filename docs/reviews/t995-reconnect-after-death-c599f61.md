# T-995 — reconnect-after-death review

Verdict: **ACCEPT** successor
`411284df7fdc6620c286d442469b9938a0df731a` for the macOS developer preview.

The source implementation closes the stale-Ready defect without widening a
Ready claim: only an authoritative `unavailable` result for the same host,
user, argv0, environment, repository, origin, and agent may cross binary and
runner-id drift. The successor adopts the bounded release disposition from the
first review: tomorrow's supported path is the source-prefix installer, and
wheel, pipx, and Homebrew remain planned.

## Evidence

- Initial candidate `c599f61`: 11 T-991 tests passed. It was rejected because
  its package-parity claim was not real.
- Successor `411284d`: 15 T-991 tests passed. New tests install both `atm` and
  `tickets` through `install.sh --prefix`, prove both links resolve to the same
  `tickets.py`, drive Ready → missing binary → unavailable through each alias,
  and prove `tickets` can clear a Ready result written through `atm`.
- Successor T-991 plus T-685/T-686 focused set: 62 passed, 1 failed. The sole failure is
  the existing custom-watcher process-liveness check; it reproduced in
  isolation on both the candidate and current main with the same empty `ps`
  command-line result.
- The successor is two commits ahead of current main and merges trivially.
- `python3 tickets.py --help` exposes `harness`; `PYTHONPATH=src python3
  src/ticket_board/cli.py --help` does not.
- The contract and installer now say the packaged CLI is outside the preview
  boundary. The misleading package-parity test was renamed; it asserts only
  that a different argv0 cannot cross the seat fence.

## Release boundary

Merge this only with public onboarding and README copy that use the same
boundary:

1. Supported: clone the repository and use `install.sh` / `install.sh --prefix`.
2. Both installed names execute the same root implementation.
3. Wheel, pipx, and Homebrew remain planned and may not be described as
   alternate official install paths.

The later package consolidation remains separate from this preview repair.
