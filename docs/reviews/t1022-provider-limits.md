# T-1022: provider usage holds

Launch scope follows the CEO's 2026-09-15 ticket note. Cross-provider routing
and account switching are deferred to successor T-1028.

## Contract

The watcher records a provider rejection in the existing agent `limit` field,
with `source: provider`, the provider's reset text (`until`), and `reset_at`
only when it can resolve that text unambiguously. The observed Claude line is:

```
You've hit your session limit · resets 7pm (Asia/Singapore)
```

Evidence source: the shared board's `agents/*0914*.watch.log` and
`agents/*0915*.watch.log` on 2026-09-15. Tests replay this output through a
throwaway-board watcher; they do not consume provider quota.

Daily clocks with an explicit IANA timezone and timezone-qualified ISO timestamps
are normalized. Missing, ambiguous, or unsupported reset formats remain held
until `atm limit <seat> --clear`. The original text stays visible; no reset is
invented. Clock-only reset dates are resolved once at detection time.

A recorded limit takes precedence over fresh work evidence. `atm who` shows
`limited` with the reset, the Team snapshot uses `LIMITED`, and the held ticket
gets a deduplicated reason explaining the retry pause. Ownership stays intact.
Directed mail remains queued. Native message wake, watcher poke, and watcher
execution (including `--force`) honor the hold.

On expiry, the agent-record lock guards removal of the hold and its failed-trigger
record. Original pending work can run again. Old pre-reset log/transcript errors
cannot resurrect the expired hold; a fresh provider rejection can.

## Validation

`tests/test_t1022_provider_limits.py`: 16 passing targeted cases, including real
Claude output at exit 0 and exit 1, reset expiry and midnight rollover, unknown
reset, explicit clear, ticket history, force suppression, queued mail, Team
snapshot, fresh-work precedence, stale transcript suppression, and healthy
telemetry/productive error quotation.

Run tests with the enclosing worker identity removed from the subprocess
environment (`TICKET_SEAT`, `TICKET_AGENT`, `TICKET_SESSION_ID`, `CODEX_SESSION_ID`,
`TICKETS_RUN_ID`, `TICKETS_RUN_NO`). Otherwise existing fixture helpers inherit
the live worker seat and their synthetic actors do not acquire their test claims.

Independent review should try a new rejection after reset and a message sent
while the seat has both a native endpoint and a watcher. Either launching a
held seat or rendering it as busy would falsify the launch contract.
