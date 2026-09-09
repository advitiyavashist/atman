# T-640 continuous wake receipt

Local no-model dry run on 2026-09-09 (Asia/Singapore), using this branch's
`tickets.py` against a new temporary Git repository and board.

## Flow exercised

1. Registered `mobile-master` with `wake_mode=continuous`.
2. Registered `grok-worker` with `harness=remote` and
   `wake_mode=continuous`; set it as CoS.
3. Posted one record carrying both a direct recipient and mention:
   `tickets msg "@grok-worker Follow up the two research papers" --to grok-worker`.
4. Queried pending under the deliberately wrong ambient identity.
5. Ran the identity-selected CoS watcher with `--once --dry-run`.

## Receipt

```json
{
  "flow": "mobile-master direct+mention -> grok-worker continuous wake",
  "pending": true,
  "messages_to_me": 1,
  "task_messages": 1,
  "wake_reason": "task_messages",
  "harness": "remote",
  "wake_mode": "continuous",
  "adapter_online": false,
  "adapter_state": "queued-offline",
  "run_start": 1,
  "run_end": 1,
  "run_exit": 0,
  "dry_run": true,
  "trigger": ["messages_to_me", "task_messages"]
}
```

The direct recipient plus mention produced one wake and one bounded dry-run
receipt. The remote adapter was correctly shown offline and its message stayed
queued. No Claude, Codex, Cursor, Grok, network, or GPU call ran.

The deterministic integration test in `tests/test_t640_continuous_wake.py`
executes the complete adapter path with two persistent fake adapters: master
posts to CoS, CoS consumes the inbox and posts an action back, master consumes
that action, and each adapter records exactly one run. That fixture also proves
identity/cwd pinning and leaves fake harness cost unmeasured.

## Release-gate matrix

All rows below run against isolated temporary boards and fake adapters; the
harness-neutral rows inspect registration and dispatch policy without invoking
the named commercial model.

| Case | Evidence |
|---|---|
| direct DM and named `@mention` | one message carrying both creates one wake |
| continuous / task-only / scheduled | DMs wake only continuous; explicit tasks wake every mode; continuous and scheduled persist by default |
| Claude / Codex / Cursor / custom | parameterized harness-neutral wake-policy test |
| remote Grok boundary | bare remote spawn fails closed and the generated manifest exposes durable polling |
| online and already running | two persistent fake adapters; a concurrent second watcher is refused by the lease |
| offline and reconnect/restart | UI reports `queued-offline`; a failed one-shot leaves work queued and a later adapter consumes it |
| duplicate delivery and replay | stable message ID deduplicates an at-least-once replay; distinct concurrent sends retain distinct IDs |
| stale escalation plus new tasks | new directed tasks become the primary wake reason and change the dispatch fingerprint; an ACK does not |
| self, ACK and broadcast loops | each remains notification-only for automatic continuous wake |
| bounded context | at most five wake summaries of at most 320 characters each |
| wrong ambient identity and cwd | persistent E2E starts each child with the selected seat and pinned working tree |
| adapter failure | failed run records its exit, suppresses an unchanged retry, and keeps a visible queued reason |
| run and cost counters | exactly one start/end per fake turn; absent harness cost remains unmeasured rather than recorded as zero |
| mobile flow | master → `grok-worker` → action/ACK → master, exactly one turn per adapter without a desktop prompt |

## Current Grok limitation

The current live `grok-worker` board record names the Cursor harness, and this
machine has no local Grok executable. This change does not claim that a real
Grok turn ran. Before activation, re-register the seat as an explicit remote
adapter and connect the generated bridge:

```sh
tickets join grok-worker --harness remote --wake-mode continuous
tickets hooks remote --agent grok-worker --prompt-kind cos
```

Until that bridge connects, the UI/API reports `wake_pending=true`,
`adapter_online=false`, and `adapter_state=queued-offline`. A bare
`tickets spawn` for the remote harness fails closed instead of running Cursor
or another local model under the Grok identity.

## Rollback

Re-register an individual seat with `--wake-mode task-only` to restore the old
DM cost boundary. `tickets spawn <seat> --stop` stops its persistent adapter.
The existing `tickets hooks <tool> --rollback ...` commands restore hook files
from their exact byte receipts; no global hook file is changed by this branch.
