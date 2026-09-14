# Recovery contract (T-977)

Interrupted work is recovered from a persisted handoff plus an ownership
lease. This is not lossless memory transfer. A same-provider restart is not
a cross-provider handoff.

## Persisted handoff

`tickets handover <id> --file <markdown>` writes:

- coordination history (`.tickets/coordination/state.json`)
- a compact `handoff` object on the ticket JSON
- a board message pointing at the record

Required headings (JSON is also accepted):

```
## completion_criteria
## work_completed
## decisions
## open_questions
## artifacts
## worktree
## checks
## next_action
## facts
## assumptions
## context_budget
## missing_artifacts
```

`facts` are observed. `assumptions` are the previous worker's beliefs.
Missing headings are listed on the record. Artifact paths that do not exist
are flagged. The document is stored under `context_budget` (default 4000
bytes, minimum 256).

`tickets pulse` shows the latest handoff on each active claim.

## Ownership lease

Claiming a ticket issues `owner_generation` and `owner_lease` on that
ticket JSON. There is no extra database.

| Case | Owner | Generation | Who may `review` / `done` |
|---|---|---|---|
| Same-provider restart | unchanged | unchanged | the same agent, new process |
| Cross-provider handoff | `assign --owner` | incremented | only the new owner |

Before reassignment the lease is advanced and the claim lock is rewritten.
A process that still holds the old generation cannot accept results:

- `review` fences every revoked previous owner after one or more transfers (A stays fenced after A→B→C); a helper who never owned the ticket may still submit (T-238)
- `done` of claimed work requires the current owner
- `done` of IN REVIEW work stays legal for the closer (master merge)
- `TICKET_OWNER_GENERATION` that does not match the board is rejected
- `save` holds `<id>.json.lock`, compares generation, writes `<id>.json.tmp`, then re-checks generation immediately before `os.replace`. A transfer published in that window wins; the stale write is discarded
- `write_ticket_handoff` uses the same lock and owner+generation recheck immediately before `os.replace` (it still writes via `mkstemp`). A Bob generation-2 transfer between Alice's read and tempfile publication wins; the handoff must not restore Alice generation 1. Coordination history of the attempted handoff stays as written
- lock order with assign/claim: `AgentLock` (`agents/<owner>.json.lock`; two owners → sorted names) then the ticket mutation lock. `save` and `write_ticket_handoff` never take `AgentLock`

Notes from a non-owner stay allowed (T-238: make a second lane visible).

## Journeys this feeds

- **INTERRUPTED** (T-976): kill a worker after a real artifact, recover the
  same owner, continue. Assert one owner, artifact intact, visible handoff,
  independently verified result.
- **INTERCHANGEABILITY** (T-976): the same continuation on a second harness.
  If that fails, narrow the public promise. Do not present a same-provider
  restart as cross-provider recovery.

Tests: `tests/test_t977_recovery_contract.py`.
