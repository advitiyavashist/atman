# T-683 native session adapters receipt

Provider-native persistent session injection on top of merged T-640 wake
semantics. Reconciles safe endpoint ideas from Opus PR #61/#67 without
importing their unsafe identity overlap.

## Proven transports

| Provider | Mode | Transport | Verified in |
|---|---|---|---|
| Claude | native | AF_UNIX inbox socket (`CLAUDE_CODE_MESSAGING_SOCKET`) | `test_claude_register_and_wake`, fake socket server |
| Codex | native | live app-server `turn/start` (queue CLI is mailbox / poll-only) | T-706 `test_codex_app_server_turn_start_is_woken` |
| Cursor | native or supervised | `tmux send-keys` into `agent persist`, or ACP `session/prompt` on a live control sock; `-p --resume` is a new paid run | T-706 persist + ACP tests; T-683 supervised fallback |
| Remote/Grok | remote | T-640 schema-2 bridge only | `test_remote_adapter_fails_closed_on_native_wake` |
| Custom/other | supervised | `tickets watch` headless subprocess | existing T-640 path |

## Not claimed

- End-to-end proof that a real idle Claude Code session resumes from a live poke
  (wire mechanics only, per PR #61 honesty).
- Cursor `agent persist` tmux path, ACP control-sock `session/prompt`, and `agent -p --resume`
  as native enqueue (resume is a paid foreground model run; seats stay supervised unless
  persist/tmux or a live ACP control sock exists).
- Real Grok/model transport (remote bridge protocol only, per T-640).

## Registration

```sh
tickets join <seat> --persistent
```

Reads harness env vars only (never typed tokens). Endpoint files live under
`~/.cache/atman/sessions/<board-hash>/<seat>.json` (dirs `0700`, files `0600`).

Capability probe fails closed with an actionable reason when transport is
missing. `tickets self` reports probe/registration status.

## Board scope of a transport (T-1107)

The harness transport is process-global: `CLAUDE_CODE_MESSAGING_SOCKET`,
`CODEX_THREAD_ID` and `CURSOR_CONVERSATION_ID` are inherited by every child of
a live session, so anything running inside one (a test, CI, `quickstart
--gate`, an in-session agent on a throwaway board) holds a working wake path
into the operator's real window. That is how a wake posted on a scratch board
was delivered into a live Claude Code session.

Rules:

- One live session **may** be joined to several boards, and each of those
  boards can wake it. A second board is a normal operator setup (product repo
  plus ops repo), not a leak, and refusing it strands the seat on "no live
  endpoint" with nothing to recover with.
- A board whose state is deliberately **isolated** — its own `HOME`, or a
  `TICKETS_CACHE_DIR` that is not `~/.cache/atman` — may not register or poke
  a transport it merely inherited. `join --persistent` refuses with the reason,
  and `wake_seat`, `has_live_native_session` and `native_wake_online` all
  refuse together, so a seat never reads "native online" while every wake is
  refused. If the account home cannot be determined from the password
  database, the environment is treated as isolated as well.
- Such a board can still use a transport that is genuinely its own by
  announcing it: `ATMAN_SESSION_TRANSPORT_BOARD=<board path>` (an
  `os.pathsep`-separated list for more than one board). The announcement names
  a board, so inheriting it refuses instead of leaking, and
  `quickstart --gate`, the test suite, and supervisor-launched spawn/watch
  children scrub it along with the transport vars.
- Ownership is never first-registrar: no board can claim a session and lock
  another one out. Each endpoint records the board it was registered for
  (by `realpath`, so a symlinked worktree, a moved repo and a relative
  `TICKETS_DIR` are the same board), and a record found under another board's
  cache is refused only while it is still live — a dead one is expired so the
  seat can re-register.

This is an accident guard. A scratch board sharing the operator's HOME and
cache can still register and wake the same readable transport; these checks
do not provide a security boundary between processes running as one user.

## Wake path

`tickets msg --to <seat>` writes the board first, then attempts native
injection when the message already wakes a polling seat (`_message_wakes` or
continuous DM/@mention). Remote/custom/no-endpoint and supervised Cursor
outcomes stay queued-offline; only a refused or rebound native injection is
recorded as adapter `failed`, scoped to that provider. Watcher exhaust
records stamp the same provider/harness. Watcher retries and terminal
`failed` are scoped from the registered workforce harness, not `_harness_of_cmd`
(`agent -p` / cursor+claude look like `custom` and must not reset the cost gate).
`join` captures the previous
workforce harness before overwrite and clears leftover (including legacy
unscoped) failure when the provider actually changes — Claude/custom to
Codex or Cursor, not only a remote rejoin. Re-joining as another harness
(including a healthy remote bridge) therefore does not report `failed` for
the new adapter. `tickets watch` / `tickets spawn` refuse to double up
on a seat with a live native endpoint. Run receipts are generation-fenced:
`spawn --stop` with no live watcher closes the receipt, and a late heartbeat
from the dead run cannot set `active=true` again. The receipt's complete
read/check/replace transaction holds a stable per-seat `.run.lock` using
`flock`, so a watcher process cannot publish a stale snapshot after an
operator process stops it. Wake delivery reserves `message_id`
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
