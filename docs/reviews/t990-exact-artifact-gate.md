# T-990 — exact-artifact review gate

Verdict: **ACCEPT** `3f2b0ea3edfd5c874ea7aecff11fb2e546245651` for integration.

The candidate makes `atm review --pr` fail closed unless the submitted commit
is pushed, belongs to the artifact repository, and is equal to or contained by
the PR head. `atm accept` and `atm reject` bind structured verdicts to the
submitted head; the author cannot approve their own work, an unrelated or
short acceptance SHA is refused, a later submission supersedes an older
verdict, and prose notes remain unstructured. `atm done` keeps its existing
close and successor-start behavior.

## Evidence

- Candidate-specific suite: `25 passed` at the exact candidate, covering both
  root `tickets.py` and packaged `src/ticket_board/cli.py`.
- Clean-current-main replay: the same `25 passed`.
- Adjacent candidate checks: `59 passed, 1 failed`; the only failure was the
  older same-second reopen chronology behavior in the historical candidate,
  outside this change. Current-main adjacent checks were `61 passed, 1 failed`;
  the only failure was a stale test that tries to claim a second active ticket
  after the one-active-ticket rule landed.
- An apparent refusal for a pushed branch containing slashes was investigated.
  The restricted review shell could not resolve GitHub; an unrestricted
  `git ls-remote` returned the exact remote head. This is environmental, not a
  review-gate defect.
- The candidate diff is clean against its base and no candidate files were
  edited during review.

## Integration note

Merge the exact candidate commit or a successor that contains it. After the
merge, keep `tests/test_t944_accept_reject.py` in the release-focused suite so
root and packaged CLI behavior cannot drift.
