# T-1012 — PR #200 `atm` language review

Verdict: **FIX** at exact head
`4c2397e126260f70628b33097327a074e3cc1e1f`.

The command migration is behaviorally safe where tested: the source-prefix
installer creates both `atm` and `tickets` links to the same `tickets.py`, work
created with `atm` is visible through `tickets`, `_worker_cmd()` emits
`$(atm prompt)`, and that executable is present after the supported install.
The merged Team/auth copy and stale-Ready recovery also remain intact.

## Launch-blocking findings

1. Generated onboarding still teaches the old command. `PROTOCOL` contains a
   split-line `` `tickets\nsprint show` `` in both `tickets.py:17192` and
   `src/ticket_board/cli.py:4775`. `atm init` copies it into both `AGENTS.md`
   and `.cursor/rules/tickets.mdc`. The new regex only matches `tickets ` on
   one line, so the contract test misses the generated defect.
2. A runtime wake explanation still tells an operator to run
   ``tickets hooks agy`` (`session_adapters.py:732`). This is user-facing and
   is outside the test's scanned template/doc set.
3. README/operator docs still lead with the compatibility name in active
   commands: `README.md:169` says ``tickets --version`` and
   `docs/onboarding/master-howto.md:94,434` do the same. The README production
   example also changes from the cloned `atman` directory to `cd ~/tickets`
   (`README.md:162`). References that explain the compatibility symlink or the
   internal `tickets.py` module are legitimate and should stay.
4. Public onboarding leaks this machine and the live Steer board rather than
   describing a reusable Atman flow. `docs/onboarding/ceo-connect.md` and
   `docs/onboarding/ceo-mac-runbook.md` include `/Users/kavana/...`, the Steer
   board path, live seat names, HOLD ticket IDs, an NER instruction, and a
   Cursor-only staffing policy. The README links this as “this Mac.” These
   files need to be moved out of the public onboarding path or rewritten as a
   provider-neutral example before the no-board-policy-leakage acceptance can
   pass.

The new test needs coverage for multiline commands, `--version`, runtime
adapter copy, generated `atm init` output, and forbidden local/live-board
identifiers. A source-only scan of selected constants is too narrow for the
promise that every current user-facing surface says `atm`.

## Independent evidence

- `28 passed, 1 deselected` — T-1010, T-809, and T-322 focused tests. The
  deselected test builds a wheel; wheel packaging is outside the source-prefix
  preview.
- `98 passed, 1 deselected` — wake, workflow/work-view, onboarding UI, team
  intro, and CEO-connect tests with local sockets/process inspection enabled.
- `23 passed` — merged T-687/T-991 Team/auth and stale-Ready regression tests.
- Independent source-prefix smoke: `./install.sh --prefix <isolated-bin>`;
  `atm create`; `tickets show/list` against the same board; generated worker
  command `claude -p "$(atm prompt)" ...`; all passed.
- `git diff --check` passed and the review worktree remained clean before this
  report.

## Minimal successor gate

Rebase onto current `origin/main` after PRs #196 and #197, preserving T-1004's
review-ending first-run loop and T-1005's board-neutral harness catalog. Fix
the four findings above, then rerun the three focused groups and the isolated
source-prefix alias smoke. The successor must additionally initialize a fresh
board and scan the generated `AGENTS.md` and Cursor rule, and PR #200 must be
mergeable at the new exact head. No full suite or provider run is needed for
this bounded copy repair.
