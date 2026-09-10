# Contributing to Atman

Atman should be understandable from a clean clone. You should not need access
to the maintainers' ticket board, model accounts, or private machines to make a
normal contribution.

## Current development path

The accepted runtime is currently Python 3.9+ with a React/TypeScript dashboard.
Atman Core is moving incrementally to Go under
[ADR-001](docs/architecture/ADR-001-go-core.md). Go code does not become the
default until the frozen differential and rollback gates pass.

For the current tree:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[contracts]'
python -m pytest -q

cd ui
npm ci
npm test
npm run build
```

If your change is isolated, run the focused test while developing, then the
relevant complete lane before submitting. Do not claim a platform, race, or
performance result from a mocked test.

## Find the right place

- User-facing command behavior currently starts in `tickets.py` and
  `src/ticket_board/cli.py`. Their duplication is known architecture debt and
  must be removed as the first migration step; do not introduce another copy.
- The versioned HTTP contract is `docs/contracts/openapi.yaml`.
- SQLite storage is under `src/ticket_board/storage/`.
- HTTP/SSE transport is under `src/ticket_board/server/`.
- Runner and harness behavior is under `src/ticket_board/runners/` and
  `src/ticket_board/adapters/`.
- The dashboard is under `ui/`.
- The target Go boundaries and supported public API are documented in
  [the module contract](docs/architecture/core-module-contract.md).
- Migration and performance gates are documented in
  [the migration plan](docs/architecture/core-migration-and-benchmarks.md).

## Change rules

1. Preserve the frozen behavior or update its versioned contract and fixtures
   in the same change.
2. Put domain behavior in one implementation. CLI, HTTP, hook, and storage
   code adapt that behavior; they do not redefine it.
3. Add dependencies only through the recorded dependency policy.
4. Keep changes small enough to review with an adversarial case. Include the
   commands and fixtures that provide evidence.
5. Never test a migration against an operator's live board. Use a copied board
   or a generated fixture.
6. Keep secrets, credentials, transcripts, live board state, and machine paths
   out of commits and test output.
7. Explain error recovery in user terms. An error must say what failed and the
   safe next action without exposing internals.

## Compatibility

The `.tickets` layout, CLI JSON and exit behavior, `/api/v1`, SSE cursors, and
public Go API are versioned product surfaces. Internal package names and
implementation details are not. Breaking a product surface requires a major
version or an explicit compatibility window with a tested migration and
rollback.

Issue reports are most useful when they include the Atman version, OS and
architecture, the smallest reproducible fixture, expected and actual behavior,
and redacted command output. Security issues should follow the repository's
security reporting policy once published rather than include sensitive details
in a public issue.
