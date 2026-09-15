# T-989 independent review: connect and reconnect

Verdict: **FIX** at
`cb0ed31` (Atman PR #118).

The original review incorrectly accepted this candidate. Its browser pass did
not test the transition where an agent first reaches Ready and its executable
then disappears. Independent T-987 tested that transition and found that
Reconnect silently returns the old Ready record. This correction supersedes
the earlier verdict; T-991 owns the bounded repair.

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
- Missing coverage in this review: remove the previously resolved provider
  binary after the seat reaches authoritative Ready. T-987 reproduced that
  exact path: the fresh probe correctly classified the agent as unavailable,
  but `merge_auth_check()` discarded it because the execution-context
  fingerprint changed from the resolved binary path to the unresolved argv0.
  `POST /auth-reconnect` then returned the previous Ready record with an
  unchanged check time.

## Required repair

1. Accept an authoritative negative reconnect for the same fenced seat when
   the provider binary disappears, without allowing a different seat or runner
   to overwrite the enrolled identity.
2. Prove Ready → unavailable updates the check time and gives a useful recovery
   action, alongside the existing first-connect, unauthenticated, wrong-seat,
   board-move, and root/package checks.
3. Re-run the focused T-685/T-686/T-687 suite at the final successor SHA and
   obtain independent review before integration.
4. T-810 owns the separate page-level `connected` to `Board synced` repair;
   this auth card already uses evidence-backed agent states.
5. T-981 owns repository-wide machine-local path cleanup. Runtime runner
   identity remains local operational data and must not be copied into public
   screenshots or documentation.
