# T-989 independent review: connect and reconnect

Verdict: **ACCEPT for integration** at
`cb0ed31` (Atman PR #118).

The candidate is one commit ahead of its merge base and 109 commits behind the
reviewed current main. A virtual merge into current main was clean. The release
integrator must preserve the candidate content, produce a new exact SHA, and
repeat the focused suite; this verdict is not evidence for unrelated changes.

## Evidence

- Candidate: 56 focused T-685/T-686/T-687 tests passed.
- Clean virtual integration with current main: the same 56 tests passed.
- Real headless Chrome against a throwaway board and fake zero-model provider
  status command:
  - Before recheck: `Login required`, `paused-auth`, and `offline` were all
    visible; ready was false.
  - After the fake provider changed to a logged-in state and the user pressed
    Recheck auth: ready became true while `offline` remained visible. Auth and
    reachability therefore stayed distinct.
  - The page contained no password input, had no horizontal overflow, and the
    response said queued work was eligible rather than claiming a model ran.
- Secret-shaped reconnect payloads, dashboard login attempts, foreign-host
  execution, non-authoritative ready records, quota, network, unsupported, and
  missing-probe cases are covered by the focused suite.

## Integration conditions

1. Merge or rebase the single candidate commit onto current main without
   changing its behavior.
2. Re-run the 56 focused tests at the final SHA. No full-suite rerun is needed.
3. T-810 owns the separate page-level `connected` to `Board synced` repair;
   this auth card already uses evidence-backed agent states.
4. T-981 owns repository-wide machine-local path cleanup. Runtime runner
   identity remains local operational data and must not be copied into public
   screenshots or documentation.
