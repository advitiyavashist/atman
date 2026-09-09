# T-634 live release and hook activation

Activated Atman commit `f523e59af461c13023b88c41f444d873d69ee0ae`
on 2026-09-09. This release contains the T-620 knowledge graph, T-633
durable hook identities, and the upgrade reconciliation that removes legacy
Cursor hooks and stale Codex identities in the same worktree scope.

## Provenance and rollback

- Required rollback release: `3a641e9e2d9435cfb49c79a3287981f45d4fb17c`
- Required rollback launcher SHA-256: `86569dbf42fb11f81dcd5cb9e0825bb5df32b5bd16e446b714f1da5fe03aa160`
- Active release: `f523e59af461c13023b88c41f444d873d69ee0ae`
- Active launcher SHA-256: `926c6fd4579120739a6a04c6e3aafa3c40ed3162d33770a33fc3d3957c10d51f`
- Global command: `/Users/kavana/.local/bin/tickets`
- Preserved 3a641 launcher: `/Users/kavana/.claude/tools/tickets-releases/previous-86569dbf42fb11f81dcd5cb9e0825bb5df32b5bd16e446b714f1da5fe03aa160`
- Immediate prior launcher: `/Users/kavana/.claude/tools/tickets-releases/previous-bb4b89676683b86899cf043f4d38026d74ad54620640c07df9f1bfd556df2ee8`

If both layers must be restored, run the hook rollback commands below before
rolling back the CLI. Then restore the CLI to the required release from this
worktree with:

```sh
python3 scripts/install_live.py --ref 3a641e9e2d9435cfb49c79a3287981f45d4fb17c --activate --expected-live-sha256 926c6fd4579120739a6a04c6e3aafa3c40ed3162d33770a33fc3d3957c10d51f
```

The four hook installers retained exact byte-level, mode-0600 rollback
receipts. One-step hook rollback commands are:

```sh
tickets hooks claude --agent sol-ceo-cto --settings /Users/kavana/.claude/settings.json --rollback
tickets hooks codex --agent codex-master --hooks-file /Users/kavana/.codex/hooks.json --rollback
tickets hooks cursor --agent cursor --worktree /Users/kavana --rollback
tickets hooks remote --agent grok-worker --wrapper /Users/kavana/.local/bin/tickets-grok-worker --rollback
```

Rollback was not executed because the active release passed its gates.

## Installed identities

| Harness | Durable identity | Installed surface |
|---|---|---|
| Claude Code | `sol-ceo-cto` | `/Users/kavana/.claude/settings.json` |
| Codex | `codex-master` | `/Users/kavana/.codex/hooks.json`, scoped to `/Users/kavana/Downloads/steer/.worktrees/sol-ceo-cto` |
| Cursor | `cursor` | `/Users/kavana/.cursor/hooks.json` and `/Users/kavana/.cursor/hooks/tickets-board.py` |
| Grok/custom remote | `grok-worker` | `/Users/kavana/.local/bin/tickets-grok-worker` and its `.hooks.json` manifest |

Each generated command freezes both `TICKET_AGENT` and `TICKETS_DIR`. The live
upgrade test set hostile ambient values and ran the installed commands from
`/private/tmp`; all four kept their configured identities. It also proved
there is exactly one generated Codex hook per lifecycle event and one generated
Cursor hook per lifecycle event. No old `0dbd18` Codex entry or legacy
`check-message-board.py` Cursor entry remains.

The duplicate cleanup matters during upgrades. An older Codex entry used the
same worktree with a different identity, while the older Cursor hook depended
on ambient identity. Keeping either would run one event twice and could
attribute its board work to two seats.

## Verification

- `tickets --version` reports the exact active commit and `verified release`.
- Hostile installed-hook run: Claude, Codex, Cursor, and Grok/custom passed
  from an unrelated working directory with wrong ambient board and identity.
- `tickets knowledge validate`: 15 nodes and 16 edges valid.
- Focused hook, knowledge, and release suite: `26 passed in 29.10s`.
- `python3 -m py_compile tickets.py` and `git diff --check` passed.
- The T-620 Cursor knowledge test now executes the installed command, including
  its required baked `--agent`, instead of bypassing the public hook contract.

The sandboxed Cursor probe first failed because the sandbox could not write
`~/.cursor/hooks/state`. Re-running that same installed hook with normal host
permissions passed. This was a test-environment permission boundary, not a
hook failure.

## Local command latency

Three runs per command from `/private/tmp`; values include Python startup,
release-manifest verification, board loading, and report rendering.

| Command | p50 | max |
|---|---:|---:|
| `--version` | 129.99 ms | 130.56 ms |
| identity hook | 144.61 ms | 144.97 ms |
| `turns --since 2026-09-06 --json` | 527.86 ms | 666.59 ms |
| `ui --json` | 729.31 ms | 1,236.47 ms |
| `util --json` | 912.46 ms | 928.47 ms |

These are local operational measurements on the current large board. They are
not pure interpreter-startup benchmarks.

## Remaining wake gate

This activation proves hook identity and lifecycle delivery. It does not yet
prove that an incoming board DM or mention causes an already-running model
session to take a turn. T-640 owns that separate delivery contract, including
user-configured continuous, task-only, and scheduled policies; exactly-once
wake deduplication; offline/reconnect behavior; adapter failure recovery; and
the mobile master-to-Grok-CoS round trip. A Grok wrapper exists, but a real
Grok session cannot be claimed as automatically woken until a valid remote
adapter is connected and that end-to-end receipt passes.
