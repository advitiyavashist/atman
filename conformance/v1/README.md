# Atman Core behavior contract v1

This directory is the compatibility boundary for replacing Atman's Python
backend. It freezes behavior, records a Python oracle result, and gives every
future implementation the same executable cases. It does not prescribe a
language, database, module graph, lock primitive, or internal data structure.

The three normative artifacts are:

- `contract.json`: numbered requirements and their independent evidence;
- `corpus.json`: portable, isolated black-box scenarios;
- `golden/python-oracle.json`: selected observable results from the accepted
  Python implementation at the pinned revision.

`docs/contracts/openapi.yaml` and `tests/fixtures/manifest.json` remain the
normative HTTP records and error shapes. The index in `contract.json` binds
them into this version rather than copying and eventually drifting from them.

## Compatibility rules

The contract covers observable semantics. Human-friendly spacing, timestamps,
generated IDs, temporary filenames, SQLite layout and implementation-specific
diagnostics are excluded unless a numbered requirement explicitly names them.
This is how the Python implementation remains the oracle without freezing its
incidental quirks.

Two observed Python quirks are explicitly outside v1: `tickets claim` can
bypass an unfinished dependency even though `tickets next` correctly gates it,
and a malformed canonical `T-*.json` file is silently omitted by some list
reads. `DEPS-001` requires every claim path to gate dependencies, and
`STORE-001` permits orphan temporary residue but never a malformed committed
record. These gaps should be repaired in the oracle and then promoted into
negative corpus cases; a replacement must not preserve them for parity.

A compatible implementation must:

1. pass every case and structured capture in the golden corpus;
2. validate every HTTP fixture against the existing OpenAPI contract;
3. preserve atomicity and fencing under process-level races;
4. read the frozen legacy board and migrate without data loss;
5. meet performance thresholds on the same machine and corpus size.

Unknown stored fields must be preserved in an archive or rejected before any
mutation. They may not disappear silently. A breaking storage or command
change requires `atman-core/v2`, a migration command, rollback evidence, and a
documented support window. New optional JSON fields are allowed when the
existing OpenAPI schema permits them; removing or changing meaning is breaking.

## Run the Python oracle

From the repository root:

```sh
python3 tools/run_core_conformance.py
```

To test another CLI and its storage adapter:

```sh
python3 tools/run_core_conformance.py \
  --command-json '["./target/release/atman"]' \
  --adapter-command-json '["./target/release/atman", "conformance-adapter"]'
```

The adapter is a test boundary. It accepts the scenario name and `--db PATH`,
then prints one JSON object. Product clients do not depend on it. The initial
scenarios are `duplicate-delivery` and `lease-fencing`; adding a scenario is a
backwards-compatible contract extension if existing captures remain unchanged.

To intentionally refresh the oracle after an accepted contract change:

```sh
python3 tools/run_core_conformance.py \
  --record conformance/v1/golden/python-oracle.json
```

Review the semantic capture diff and measured latency before committing it.
Never bless a candidate merely to make its divergence disappear.

## Behavioral boundaries

### Storage and claims

Canonical records become visible atomically. A crash may leave an unrecognized
temporary file, which readers ignore; it may not expose half a ticket or erase
the prior committed value. A claim races on the persisted state, not an
in-process mutex. Exactly one eligible agent wins. Dependency checks, capacity,
state transition and audit append belong to the same transaction.

### Objectives and scheduling

An objective records the desired outcome and an observable exit criterion.
Achieved, blocked and replaced are terminal outcomes with evidence or reason.
Dependency-blocked work stays unclaimable. Master and chief-of-staff sessions
may be continuous; ordinary unattended workers stop after their configured
bounded run. That policy does not weaken lease fencing.

### Messages, mentions and wakes

The authenticated identity is the author. Message bodies are immutable; a
correction is a new message linked to the old one. Exact mentions create
targeted delivery, while quoted code, emails and doubled `@@` do not. An
explicit task delivery creates actionable work until inbox acknowledgement.
The transport is at-least-once: request replay and wake-job creation deduplicate
without claiming exactly-once model or shell execution. Receipt and run states
remain separate.

### Identity and hooks

The installer bakes the board path and agent identity into each harness hook.
Ambient `TICKET_AGENT` and `TICKETS_DIR` cannot redirect it. Writes and rollback
receipts are atomic. A rollback restores exact prior bytes and fails closed if
the installed file changed after installation. Hook input is treated as data,
never interpolated into a shell command.

### Leases, time and recovery

One agent has at most one live runner lease per project. Expired takeover
increments an epoch. Every heartbeat and terminal transition carries that
epoch, so a delayed old runner is fenced out even when clocks disagree. Stored
times are UTC. Knowledge dates more than five minutes ahead are rejected.
Backup uses a consistent snapshot; restore verifies before replacement and
removes stale journal sidecars.

### Knowledge and path safety

Ticket state and the knowledge graph are separate. Tickets carry exact
`knowledge:<node-id>` references, and context expansion remains bounded.
Every read and write resolves containment first. A symlink, traversal, case
alias or temporary-directory trick cannot move a board, worktree, hook or graph
operation outside its declared root.

### JSON and errors

Commands documented with `--json` print exactly one JSON value on stdout.
Failures are non-zero and carry a stable code on the HTTP/storage surface;
human CLI diagnostics name the failed subject. Null versus omitted follows
the OpenAPI schema. No success object is printed after a failed mutation.

## Performance evidence

The corpus records p50 and p95 wall time but compares candidates against a
threshold, not the oracle's exact millisecond value. The v1 floor is a warm
250-ticket `tickets board --quiet` p95 below 1000 ms on the reference laptop.
This is a regression tripwire, not a published production throughput claim.
Race correctness, recovery and schema checks still run even if performance is
better.
