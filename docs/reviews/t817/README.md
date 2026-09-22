# T-817 Core migration acceptance repair

Repairs T-805 REQUEST FIX findings against current `origin/main` while preserving
ready-lane checks in `try_claim` / `cmd_claim`.

## Changes

- STORE-001: committed `T-*.json` fail closed (`read_ticket_file`); dangling
  symlinks become `corrupt ticket … dangling symlink` without traceback.
- Claim-lock cleanup: `try_claim` releases the O_EXCL lock on every refusal path
  (including corruption / unreleased deps) via `finally`.
- Mutation boundary: `save()` validates sibling committed records before any write
  (covers `update`/`note`/`create` with a corrupt neighbor).
- DEPS-001: explicit claim/status/assign refuse unfinished deps; open-pred message
  is `has unfinished dependencies` (done-unaccepted keeps accept-gate wording).
- Corpus: plan fixtures carry cause/change/proof (ready lane); dependency case uses
  `done --release-unverified`; executable STORE-001 case added; warm board p95 gate
  kept under 1000 ms.
- Runner: fixture gitignore for dirty isolation; agents/MASTER/CONTEXT names-only
  snapshots; corrupt JSON tolerated in board-tree hashing.

## Proof

```text
python3 -m pytest -q tests/test_malformed_ticket_json.py tests/test_claim_dependencies.py tests/test_t642_core_conformance.py
# 21 passed
python3 tools/run_core_conformance.py
# 17/17 passed, warm p95 < 1000ms; exact golden parity on repeated runs
```

Independent reacceptance required before T-643.
