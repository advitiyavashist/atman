# Atman Core migration, quality and benchmark gates

The migration is complete only when a reviewer can reproduce behavior,
performance, installation, and rollback without private board context. Shipping
new-language code is not itself progress toward cutover.

## First remove the Python duplication

Before porting write behavior, make `src/ticket_board/` the only Python
implementation:

1. Port the accepted root-only behavior into package modules behind the T-642
   black-box corpus.
2. Replace root `tickets.py` with a launcher of at most 25 lines that prepends
   the repository's `src` directory and calls `ticket_board.cli.main()`.
3. Delete root `ticket_coordination.py` and `board_backup.py`; import the package
   modules everywhere.
4. Change immutable-release export to package files plus the launcher. Generate
   the release manifest from the Git tree exactly as it does today.
5. Remove all two-entry-point tests. Replace them with one test that proves the
   root launcher and installed console script resolve the same package bytes,
   followed by the shared black-box corpus.
6. Add a CI check that rejects a root Python file over the launcher line budget
   and rejects duplicate top-level function bodies across delivery paths.

Do not generate one copy from the other. Generated duplication still creates
two review surfaces and makes installed provenance harder to explain.

Once Go becomes canonical, the Python package moves under `compat/oracle/` and
is invoked only by differential tests and the installed rollback launcher. It
does not receive features independently.

## Staged delivery

| Stage | Owner ticket | End state and gate |
| --- | --- | --- |
| 0. Freeze | T-641 + T-642 | ADR accepted; language-neutral fixtures cover current accepted behavior and failure cases. |
| 1. Scaffold | T-643 | Go module and package skeleton; `version`, `self`, fixture loader, six-platform build matrix, docs and checks pass. Python remains live. |
| 2. Storage | T-644 | Existing board resolution, JSON, locks, state-plus-outbox transaction directories, SQLite transactional outbox, recovery, backup/restore and race primitives pass differential and crash tests. No live cutover. |
| 3. Domain | T-645 | Tickets, graph, objectives, roles, routing inputs and reviews use one service implementation and pass fixtures. |
| 4. Messages | T-646 | Channels, mentions, inbox watermarks and idempotent message-to-wake behavior pass T-640 and the language-neutral corpus for all three wake modes. |
| 5. Runners | T-647 | Claude, Codex, Cursor, Grok, and custom adapters support user-selected continuous, task-only, and scheduled modes; leases, hook events, liveness and recovery pass deterministic fake-harness tests on macOS and Linux. |
| 6. Service | T-652 | All 34 frozen API paths, SSE replay/heartbeat and embedded UI use the same domain services and pass OpenAPI conformance. |
| 7. Compatibility binary | T-648 | `tickets` compatibility command and `atman` alias are the same versioned binary; platform archives, checksums and exact Python rollback are reproducible. |
| 8. Public library | T-649 | Public Go API, package docs, examples and contributor path are reviewed; no internal implementation leaked into the API. |
| 9. Shadow | T-650 | Copied-board differential replays show zero unexplained semantic divergence. The live board still has one writer. Rollback drill passes. |
| 10. Alpha cutover | T-651 | Reviewer approves evidence, activates Go for a canary board, observes it, then cuts the default. Python fallback survives at least two releases. |

Any stage can ship internally without changing the default executable. A failed
gate returns to the previous stage; it does not weaken the corpus.

## Differential correctness gates

The Python oracle and Go candidate receive separate byte-identical board copies
and the same command/event sequence. Compare after every operation:

- stdout, stderr, structured JSON, and exit status;
- complete board tree paths, modes, and content after canonical normalization;
- record versions, event and message ordering, inbox watermarks and wake jobs;
- subprocess receipts, lease transitions, hook identity and recovery outcome;
- HTTP status, headers, bodies, authorization and idempotency responses;
- SSE event ids, replay window, heartbeat shape and snapshot fallback.

Required adversarial scenarios include killed writers at every durable-write
boundary, 32-process claim races, symlink escapes, ambient Git variables,
duplicate message delivery, stale leases, clock skew, cursor retention gaps,
malformed/unknown fields, older board versions, and an interrupted install.

Cutover requires **zero unexplained semantic mismatches**. A deliberately fixed
Python bug is encoded as a versioned contract change with before/after fixtures;
it is never hidden in the rewrite diff.

### Crash and outbox gate

T-644 injects a process kill before and after every file-board protocol step:
payload write, payload flush, manifest flush, staging-directory flush, committed
directory rename, committed-parent flush, each state projection, checkpoint
replacement, and compaction cleanup. After reopening, each attempted operation
must have exactly one of two observable outcomes:

1. no committed transaction, no state change, no audit entry, and no event; or
2. one committed transaction whose complete state, audit, stored result, and
   outbox events recover and materialize exactly once.

There is no accepted state-only or event-only outcome. Reopening twice must
produce identical bytes and cursors. Corrupt checksum, sequence gap, foreign
after-image, and unsupported durable-replace cases must refuse writes and expose
the recovery error without advancing the checkpoint.

SQLite fault injection covers every statement before commit, commit followed by
a lost response, process death before the SSE notification, and restart with an
undrained outbox. Before commit, no row is observable. After commit, domain,
audit, idempotency, and outbox rows are all observable; retry returns the stored
result. SSE tests disconnect after receipt but before client persistence and
prove at-least-once replay with stable ids, then cross the retention floor and
prove snapshot fallback.

### Wake-mode gate

T-646/T-647 run the same table for Claude, Codex, Cursor, Grok, and a fake custom
harness. Every seat is tested under all three user-selected modes:

- `continuous`: DM, explicit mention, task message, and assignment each wake a
  live session immediately; an offline wake queues and delivers once on
  reconnect.
- `task-only`: assignment and `--task` start one bounded run; an ordinary DM or
  mention remains unread without starting a model turn.
- `scheduled`: an externally invoked cadence consumes durable ordinary DMs and
  mentions in one bounded turn; an assignment or `--task` starts an immediate
  run. The test supplies the cadence event because Atman does not own a cron
  expression or next-run deadline.

The matrix also proves DM-plus-mention deduplication, self/broadcast suppression,
one live session lease, identity pinning, bounded context, failure receipts,
recovery after process death, and a mode change with queued work. Master and CoS
receive `continuous` only as a default registration value; tests override both
roles to the other modes and configure ordinary workers as continuous.

## Performance protocol

T-643 adds a repository-owned `bench` command that writes JSON. Each record
includes commit, dirty state, Go version, build flags, OS, architecture, CPU,
RAM, filesystem, board fixture hash, timestamp, sample count, and metric units.

- Run a minimum of 41 measured subprocess starts after five warmups.
- Report p50, p95 and maximum; do not report only a mean.
- Measure cold install and first start separately from warm filesystem-cache
  runs.
- Measure process RSS only after the server is ready and again with 1, 25, and
  100 SSE clients.
- Run storage benchmarks against 10, 1,000, and 10,000 ticket boards.
- Run contention with 2, 8, and 32 processes, not goroutines alone.
- Store raw JSON under `benchmarks/results/`; a Markdown table is a derived view.
- Compare on the same machine and copied fixture. Never compare a Go result on
  one laptop with a Python result on another.

### Alpha performance gates

These are pass/fail budgets, not marketing claims:

| Measurement | Alpha gate |
| --- | ---: |
| `atman --version` warm p95 | <= 25 ms |
| Snapshot of 1,000 tickets warm p95 | <= 100 ms |
| Uncontended claim on 1,000 tickets p95 | <= 100 ms |
| 32-process race | correct result and <= 2x serialized single-process time |
| Loopback server ready p95 | <= 75 ms |
| Idle loopback server RSS p50 | <= 40 MiB |
| 100 idle SSE clients incremental RSS | <= 30 MiB |
| SSE committed-event delivery p95 | <= 100 ms |
| Release binary including UI, stripped | <= 30 MiB per platform |

The current Python baselines measured for this ADR were 105.9 ms p50 / 112.4
ms p95 for `--version`, and 126.5 ms median ready time / 55.2 MiB median RSS for
the one-ticket local UI server. These establish the initial comparison only;
T-642 owns the reproducible board fixtures and broader baseline.

Correctness gates precede performance gates. A faster command that changes
message delivery, ordering, errors, or durability fails.

## Dependency policy

Atman Core starts with the Go standard library. A production dependency is
accepted only when it removes substantial correctness risk or a frozen protocol
would otherwise be reimplemented poorly.

Initial allowlist:

- `github.com/spf13/cobra` in `internal/cli` for the 64-command compatibility
  surface. No domain package imports it.
- `github.com/gofrs/flock` behind `internal/lock` after its cross-process suite
  passes on macOS, Linux, and Windows.

Everything else requires a short dependency record containing purpose,
alternatives, license, maintainership, transitive modules, binary-size delta,
security history, and removal plan. In particular:

- use `net/http`, `http.Flusher`, and `embed.FS`; no web framework;
- use `log/slog`; no logging framework;
- use constructors; no dependency-injection framework;
- use `database/sql` and explicit queries; no ORM;
- do not use CGo in the default release;
- treat an OpenAPI generator as a pinned build tool whose generated output is
  committed, never as a runtime dependency;
- keep `go.sum`, release SBOM, license inventory, `govulncheck`, and provenance
  attestation in the release gate.

Pin exact direct versions. Automated update PRs must pass conformance, race,
cross-build, binary-size, and benchmark regression checks before merge.

## Coding and review conventions

- `gofmt`, `go vet`, `go test ./...`, `go test -race ./...`, and the
  language-neutral conformance runner are required. CI is the specification;
  a local convenience tool cannot impose an undocumented rule.
- Packages have one purpose and a package comment. Avoid `util`, `common`,
  `helpers`, `manager`, and catch-all `types` packages.
- Prefer plain functions and concrete types. Define a small interface where it
  is consumed when a boundary needs substitution; do not create an interface
  for every struct.
- Constructors validate required dependencies. No mutable package globals.
- Keep transport mapping at transport boundaries and platform behavior in files
  with build tags. Domain code is platform-neutral.
- Tests are table-driven for state cases, black-box for public packages,
  cross-process for locking, and end-to-end only for behavior that crosses a
  real boundary. A test must use a case the implementation did not choose.
- Examples compile in CI and show one task each. Public symbols require useful
  doc comments and an executable example where behavior is not obvious.
- Pull requests name the user-visible or contract outcome, evidence commands,
  benchmark deltas when relevant, and rollback impact.

## Release and rollback

Release archives contain one immutable executable, license, checksums, SBOM,
and provenance attestation. `atman` is the product name; `tickets` is a
compatibility filename for the same bytes through 1.x. The binary reports its
source commit, build toolchain, contract version, and dirty state through
`atman self`.

Activation is an atomic launcher replacement. It records the prior verified
artifact and never overwrites unknown bytes. Rollback swaps the launcher to the
recorded Python artifact; because no live board is dual-written and the Go
writer preserves the versioned board contract, rollback requires no reverse
data migration.
