# T-683 native session adapters receipt

Provider-native persistent session injection on top of merged T-640 wake
semantics. Reconciles safe endpoint ideas from Opus PR #61/#67 without
importing their unsafe identity overlap.

## Proven transports

| Provider | Mode | Transport | Verified in |
|---|---|---|---|
| Claude | native | AF_UNIX inbox socket (`CLAUDE_CODE_MESSAGING_SOCKET`) | `test_claude_register_and_wake`, fake socket server |
| Codex | native | `codex queue --thread <id> --message <text>` | `test_codex_queue_wake_uses_thread`, subprocess stub |
| Cursor | supervised | identity bind only; `agent -p --resume` is a paid foreground run, not enqueue | `test_cursor_resume_is_not_native_enqueue` |
| Remote/Grok | remote | T-640 schema-2 bridge only | `test_remote_adapter_fails_closed_on_native_wake` |
| Custom/other | supervised | `tickets watch` headless subprocess | existing T-640 path |

## Not claimed

- End-to-end proof that a real idle Claude Code session resumes from a live poke
  (wire mechanics only, per PR #61 honesty).
- Cursor `agent persist` tmux path and `agent -p --resume` as native enqueue (resume is a paid
  foreground model run; seats stay supervised unless a real queue transport exists).
- Real Grok/model transport (remote bridge protocol only, per T-640).

## Registration

```sh
tickets join <seat> --persistent
```

Reads harness env vars only (never typed tokens). Endpoint files live under
`~/.cache/atman/sessions/<board-hash>/<seat>.json` (dirs `0700`, files `0600`).

Capability probe fails closed with an actionable reason when transport is
missing. `tickets self` reports probe/registration status.

## Wake path

`tickets msg --to <seat>` writes the board first, then attempts native
injection when the message already wakes a polling seat (`_message_wakes` or
continuous DM/@mention). Remote/custom/no-endpoint and supervised Cursor
outcomes stay queued-offline; only a refused or rebound native injection is
recorded as adapter `failed`. `tickets watch` / `tickets spawn` refuse to double up
on a seat with a live native endpoint. Wake delivery reserves `message_id`
in-flight under the seat lock before poke so concurrent callers cannot double-inject.

## Identity

`lifecycle=persistent|ephemeral` is stored on the workforce record, separate
from `wake_mode`, `session_id`, harness, and watcher lease. `--persistent`
implies lifecycle=persistent and registers a native endpoint. `--lifecycle`
alone does not require a native transport.

Master/CoS default to persistent+continuous unless the user sets explicit
values. Ordinary seats default ephemeral+task-only.

A native session fingerprint (Claude socket, Codex thread, Cursor session id)
binds to exactly one `agent_id`. Re-joining the same seat while the endpoint
is still live requires the current `lease_id` (`TICKETS_SESSION_LEASE`) or the
same session fingerprint; otherwise wait until PID death or heartbeat TTL.
Another seat with the same transport is refused. Stale same-seat records may
be replaced without the old lease. Unconditional `os.replace` is not
ownership. PID-less Codex/Cursor endpoints go **offline** after a heartbeat
TTL and are not advertised `adapter_native_online`; the thread/session identity
is retained so the first later poke or `join --persistent` can reconnect.
Codex `SessionStart` / `UserPromptSubmit` hooks refresh that heartbeat only when
the current `TICKETS_SESSION_LEASE` or `CODEX_THREAD_ID` matches the stored
identity. A crash during poke reclaims a stale in-flight `message_id` after
`TICKETS_NATIVE_INFLIGHT_TTL_SECS` (default 30s) so the durable wake can retry.
Ephemeral watch/retire teardown removes the endpoint; those seats are not
shown as reachable after exit.

`tickets msg` pokes only when the workforce harness maps to the endpoint
provider. A leftover Claude socket on a remote/Grok or Codex seat is never
injected; the stale endpoint is removed. Grok stays on the T-640 remote
bridge.

Installed releases export `session_adapters.py` next to `tickets.py`
(`scripts/install_live.py` FILES + smoke `join --persistent`). A staged
snapshot that omits it fails closed as `ModuleNotFoundError`.

Register/rebind is serialized on a per-seat exclusive lock (`O_EXCL` lock
file + `fcntl.LOCK_EX`) and a fingerprint lock for the provider/session
identity so two seats cannot both bind the same transport. Delivery `touch`
is a no-op unless lease_id and fence still match the poke. `wake_seat` retries
the same durable `message_id` up to three times; after exhaustion the native
endpoint is dropped (honest `failed`, not fake `retrying`) so supervised
recovery can run.

`tickets watch` finalizes an in-flight run on SIGTERM: the child is
terminated, `active=True` is cleared (`interrupted`, rc 143), and a matching
`run_end` trajectory receipt is written even if the signal arrived between
`_run_begin` and `_run_end`.

`reachable` requires a live native endpoint, an active supervised watcher, or a
live remote lease. `lifecycle=persistent` only means the seat is intended to
remain; persistent+offline is known but unreachable/queued.

## UI fields

`board_snapshot` agents include `agent_id`, `lifecycle`, `reachable`,
`adapter_provider`, `adapter_mode` (`native` | `supervised` | `remote`),
`adapter_native_online`, `adapter_delivery`, `adapter_session`, and
`adapter_usage` (`unmeasured` for native pokes; remote receipts stay on the
T-640 bridge fields). The Team idle lane omits exited ephemeral seats.
