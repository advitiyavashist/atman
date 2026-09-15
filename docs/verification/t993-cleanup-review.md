# T-993 independent cleanup review

Verdict: **FIX**

Reviewed Atman PR #177 at exact SHA
`4c62164b793e480e70d7ab98d52bcaf611e1198a`, based directly on local
`origin/main` `e201436ea72b21601c94811347ed90ea19cae705`.

## Blocking finding: watcher accepts a drifted packaged release

The cleanup makes `src/ticket_board/ticket_coordination.py` and
`src/ticket_board/board_backup.py` canonical for the root launcher, and
`scripts/install_live.py` records the package tree in `release.json`. However,
both copies of `_t427_verified_sha()` still inspect only these legacy manifest
keys:

```python
("tickets.py", "ticket_coordination.py", "board_backup.py")
```

For a new-format release the latter two keys are absent, so the function skips
them and returns the release commit after hashing only `tickets.py`. This is a
behavior and integrity regression because `watch_idle_reexec()` trusts that
return value before replacing a running watcher, while `tickets.py` now imports
the unverified packaged coordination module.

Adversarial reproduction against the real staged candidate release:

1. Stage exact `4c62164` with `scripts/install_live.py`; its smoke passes.
2. Change one byte in
   `src/ticket_board/ticket_coordination.py` under the staged release.
3. Run the staged `tickets.py --version`: it correctly reports
   `tickets DRIFTED release 4c62164... (src/ticket_board/ticket_coordination.py)`.
4. Call `_t427_verified_sha(staged/tickets.py)`: it incorrectly returns the
   full exact SHA `4c62164b793e480e70d7ab98d52bcaf611e1198a`.

The existing T-427 fixture copies the package tree but `stamp_release()` writes
only the two root `FILES`, so its tamper checks do not exercise the files the
root launcher now imports.

Smallest repair: make `_t427_verified_sha()` validate every safe path declared
in `release["files"]` (in both `tickets.py` and `src/ticket_board/cli.py`), and
add a T-427 test that a same-size or content tamper of
`src/ticket_board/ticket_coordination.py` prevents the hop. At minimum, require
and validate the canonical package modules in new-format manifests while
preserving old flat-manifest compatibility.

## Cleanup scan finding

The public onboarding host/path scans pass, but the author-specific-name guard
requires a `.worktrees/` prefix and therefore misses bare names. Two public
reader paths still expose the local worktree/shim name `sol-agy-harness`:

- `docs/connect-agy.md:48`
- `docs/onboarding/ceo-connect.md:35`

Replace those with a generic stale/non-project shim description, and extend
the regression scan so the named forbidden identifiers also fail without a
`.worktrees/` prefix.

## Verified claims

- Root `board_backup.py` and `ticket_coordination.py` are deleted; the packaged
  modules are the only checkout import locations.
- The internal artifacts are under `docs/internal/` and are not linked from
  `README.md`, `docs/first-session.md`, `docs/onboarding/`, or `landing/`.
- The ownership inventory is accurate at this SHA: root `tickets.py` is 18,089
  lines, packaged `cli.py` is 5,374 lines, the package has 57 Python files, 45
  `cmd_*` handlers are shared, and the listed 33 monolith-only handlers match
  the source.
- Root script, `python -m ticket_board`, direct packaged `cli.py`, and an
  offline-built wheel all expose working help. The wheel `atm`/`tickets`
  scripts import both canonical modules from `site-packages`; wheel join and
  root/wheel board-backup succeed on throwaway boards.
- `tests/test_t981_single_implementation.py`: 8 passed.
- Paired source/install/T-427 tests have the same result on candidate and clean
  main: 15 passed, 3 sandbox failures caused by denied `ps` access. A paired
  same-location T-273 run is also identical: 12 passed, 6 existing failures.
- `git diff --check` passes. The requested public onboarding scan has zero
  Vercel/hosted-app, loopback, or `file://` URL matches; the author-home scan
  has no real operator handle outside the allowed fixture/internal areas.

No provider calls, live-board test mutation, full suite, merge, or
implementation changes were made.
