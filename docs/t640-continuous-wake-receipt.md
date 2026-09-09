# T-640 continuous wake receipt

Local no-model protocol run on 2026-09-09 (Asia/Singapore), using this branch's
root `tickets.py` against isolated temporary Git repositories and boards.

## Proven remote flow

The deterministic integration test registers `mobile-master` and `grok-worker`
as `harness=remote`, installs separate identity-pinned wrappers, and connects
each with `tickets remote register`. It then executes:

1. mobile/master posts one record containing both `--to grok-worker` and
   `@grok-worker`;
2. two consumers race `remote next` with the same lease; one receives the
   atomic claim and the other receives `busy`;
3. Grok CoS records `remote start`, reads its inbox, posts an action to master,
   and records `remote end` with 120 input tokens, 24 output tokens, and
   `$0.0123` reported cost;
4. master claims the reply, starts, reads it, posts an ACK, and ends; and
5. Grok polls again and receives `idle`, proving the ACK did not create a loop.

The trajectory has exactly two `run_start` and two `run_end` events, one pair
per seat. The reported Grok usage is attributed to its remote run. Master's
unreported usage remains explicitly unreported rather than becoming a fake
zero. No Claude, Codex, Cursor, Grok, network, or GPU model call runs in this
fixture; the bridge protocol itself is what is under test.

## Release-gate matrix

| Case | Deterministic evidence |
|---|---|
| DM plus named mention | one message record, one wake claim |
| continuous / task-only / scheduled | ordinary DM wakes only continuous; explicit task/assignment gates remain cost-authorized |
| Claude / Codex / Cursor / custom | parameterized root-runtime policy test; no commercial model is invoked |
| remote Grok boundary | bare remote spawn fails closed; schema-2 wrapper protocol is exercised end to end |
| exclusive adapter | second registration, including reuse of the same public bridge ID, is refused while the bearer lease is live |
| fencing | replacement increments the fence and the old bearer cannot heartbeat or end a run |
| atomic claim | concurrent consumers sharing one valid lease receive one `wake` and one `busy` |
| offline / reconnect | release makes the adapter offline; a new bridge registers with a higher fence and claims queued work |
| uncertain started run | reconnect reports `recovery-required`; an explicit retry closes the abandoned receipt before a new run |
| bounded retry | local and remote failures stop at their configured attempt cap (default three), then show durable `failed` with work still queued |
| manual recovery | authorized remote retry grants a fresh bounded budget; a new local trigger clears a terminal local failure |
| duplicate replay | stable message ID deduplicates an at-least-once replay |
| self / ACK / broadcast | notification-only; no automatic wake fingerprint |
| bounded context | both direct and leadership-stuck summaries keep the newest five entries at no more than 320 characters each |
| identity | wrapper-selected identity overrides wrong ambient identity; no board-wide last-joined identity exists |
| status | UI/API reports bridge online, heartbeat, fence, run, attempts, retry/failed state, and queued-offline reason without exposing the lease secret |

## Validation result

The focused protocol and wake-policy file passes **22/22** tests. The broader
release selection passes **188** tests covering existing wake behavior,
dashboard launch receipts, objective bounds, hook identity, message replay,
watch retries and teardown, trajectories, cost capture, packaging, and live
installation.

That broader selection also reports three failures in
`tests/test_t427_watch_reexec.py`: its reexec fixtures launch a default
one-shot task-only watcher but assert that it remains persistent. The same
three nodes fail unchanged against an isolated archive of `origin/main` at
`cc8cb2b`; they are recorded as baseline debt rather than T-640 regressions.
The initial sandboxed pass also blocked `ps` and localhost sockets, so the
reported release result comes from the repeated run with those test
capabilities available.

## Protocol

Generate the wrapper and JSON manifest:

```sh
tickets join grok-worker --harness remote --wake-mode continuous
tickets hooks remote --agent grok-worker --prompt-kind cos
```

The bridge follows manifest schema 2: `register` → repeated `heartbeat` and
long-poll `next` → `start` → model/session turn → `end`. `next` both waits and
atomically claims; `tickets pending` is only a read and is not described as a
long-poll or claim. The lease ID is a bearer secret; every mutation also checks
the monotonically increasing fence. A started run whose lease is replaced is
never replayed automatically because its external effects are uncertain.

## Current Grok limitation

This repository now supplies the bridge-side protocol, but this machine still
has no local Grok executable and no configured remote Grok transport. A real
Grok turn has therefore not been observed. Until a remote service registers,
the UI/API reports the committed wake as `queued-offline`; Atman never runs
Cursor or another local model under the Grok identity.

## CLI scope and rollback

The wake, watcher, hook, UI, and remote-adapter commands in this receipt belong
to the root/live single-file runtime installed by `./install.sh` or
`./install.sh --live-release`. The `pyproject.toml` console script currently
points at the smaller `src/ticket_board/cli.py` core-board implementation and
does not expose these runtime commands. Do not use the pip entry point for this
feature until those entry points are unified.

Re-register a seat with `--wake-mode task-only` to restore the explicit-task
cost boundary. `tickets spawn <seat> --stop` stops a local watcher. A remote
bridge releases its fenced lease with `tickets remote release`; after TTL
expiry it also appears offline. `tickets hooks remote --rollback` restores the
exact prior wrapper and manifest bytes. This branch does not activate a live or
global hook.
