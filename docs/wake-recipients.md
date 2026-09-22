# Wake recipients: what each harness can actually receive

A wake receipt is a claim about another process. This page records what was
measured, per harness, so `atm msg` never prints `woken` for a seat that
did not start a turn. It feeds the T-812 sender x recipient matrix.

The sender never changes the receipt: `atm msg` runs in the sender's
process and uses the *recipient's* transport, so a Codex sender and a Claude
sender get the same label for the same seat.

| recipient | transport | `woken` means | evidence |
| --- | --- | --- | --- |
| Claude | `CLAUDE_CODE_MESSAGING_SOCKET` JSON line | see T-857 | T-857 |
| Codex | app-server control socket (`thread/loaded/list` + `turn/start`) | see T-857 | T-857 |
| Cursor | `tmux -L cursor-agent send-keys` into a managed `agent persist` session | the typed line is a new row in the chat store | T-861, below |
| Agy (Antigravity) | none -- supervised | n/a: the label is `supervised (...)` | T-861, below |

## Cursor: `agent persist`, not the default tmux server

Measured against `cursor-agent 2026.09.10-fd3934a`
(`src/persistence/persistent-session.ts` in the shipped bundle):

* every tmux call is
  `tmux -u -L <CURSOR_AGENT_TMUX_SERVER_NAME|cursor-agent> -f /dev/null ...`,
  run with a scrubbed env (`PATH HOME SHELL USER LOGNAME LANG TERM COLORTERM`,
  `LC_*`) plus `TMUX_TMPDIR=/tmp`. The socket is `/tmp/tmux-<uid>/cursor-agent`;
  the user's own sessions live on `.../default` and are a different server.
* a session is cursor's only when tmux reports `@cursor_managed=1` and
  `@cursor_session_version=1`; it also carries `@cursor_workspace_hash` and
  `@cursor_chat_id`.
* inside a persist pane the CLI exports `CURSOR_AGENT_PERSIST_SESSION=<name>`,
  which is how a seat knows which session it is.
* `agent persist` needs a real pty and **refuses to create a managed session
  when `$TMUX` is set** -- it silently runs the agent inline instead
  (`if (U.TMUX) return void be(...)`). Spawn it from a pty that is not tmux.

What this replaced (T-861): the adapter read `$TMUX` + `tmux display-message`
and then ran bare `tmux send-keys`, i.e. it asked the **default** server. On a
real persist seat that either failed or typed board mail into an unrelated
session of the operator's that happened to share the name.

### `agent acp` is not a control socket

`agent acp` exists but is hidden and starts the agent as an ACP server **on
stdio** (`command("acp", {hidden:true}) -> runAcp`), i.e. a new process and a
new run. The 2026.09.10 bundle contains no `acp-control` string and opens no
control socket, so the adapter's old default
`~/.cursor/acp-control/acp-control.sock` could never exist; it only made
`native_inject` look true on a seat with no transport. The ACP path now
requires an operator to point `CURSOR_ACP_CONTROL_SOCK` at a bridge they run,
and is documented as unverified against a shipped Cursor build.

### Why `send-keys` alone is not a wake

`tmux send-keys` exits 0 once the bytes reach the pane's tty. A dead pane, a
permission modal or a dropped keystroke all still exit 0. So the receipt waits
for the injected line to appear as a new row in the chat store,
`~/.cursor/chats/<dir>/<chat id>/store.db` (table `blobs`, one plaintext-JSON
message per row -- the store is in WAL mode and is read read-only):

* line becomes a new row -> `woken`
* typed, no row within `TICKETS_CURSOR_EVIDENCE_SECS` (default 6) ->
  `delivered-unconfirmed`, and the endpoint heartbeat is **not** refreshed
* no managed session (stopped, or never on the managed server) ->
  `supervised (...)`, and `atm who` says `reachable=no`

The `@cursor_workspace_hash` tmux tag is not the `~/.cursor/chats/<dir>` name,
so the store is located by chat id.

The payload is typed as a single line: `send-keys -l` is literal, and the
newline inside the wake payload would have submitted half a prompt.

### Live check (T-861)

Throwaway board, one real persist seat, zero operator keystrokes:

* idle seat, **Codex** sender: `wake: cursor-recipient -> woken`; the pane
  started a turn and the agent posted the nonce back to the sender.
* busy seat (mid-turn), **Claude** sender: `woken`; second nonce posted back.
* after `agent persist stop`: `supervised (... is not a managed agent persist
  session on tmux -L cursor-agent ...)`, `reachable=no`.

### Neither the sender nor a busy pane is a second axis

The T-812 matrix reads as sender x recipient, but only one of those is a real
variable, and both halves are now pinned by tests rather than by re-running a
manual grid:

* **Sender.** `wake_seat(board, seat, text, harness=None, message_id="")` takes
  no sender argument at all and branches on the *recipient's* `ep["provider"]`
  (`harness` is the recipient's too -- a mismatch against the stored provider is
  refused). The one sender-sensitive gate is `wake_refusal` ->
  `borrowed_transport`, and it fires only when the recipient endpoint's identity
  equals the sender's ambient var *of the same provider* -- a genuine self-wake,
  not a Claude-vs-Codex difference. Pinned by
  `test_cursor_wake_is_the_same_from_a_codex_and_a_claude_sender`, which spawns
  two real subprocess senders and asserts byte-identical receipts.
* **Busy pane.** `session_attached` is the only busy/idle signal tmux gives us.
  `cursor_persist_sessions` parses it into `row["attached_clients"]` and nothing
  ever reads it back, so a pane mid-turn and an idle one cannot diverge. Pinned
  by `test_cursor_wake_is_the_same_into_a_busy_and_an_idle_pane`, which proves
  the adapter *can* see the difference (0 vs 3 clients) and still emits the same
  receipt and the same keystrokes.

The second one matters beyond bookkeeping: a future caller that started gating a
wake on "looks busy" would silently drop wakes, and that test is what fails.

## Agy (Antigravity): supervised, and it says so

Measured against `agy 1.2.2`:

* the binary opens no local control socket or RPC for a running session;
* `agy remote-control start|status|stop` is a **cloud** daemon (WebRTC
  signalling) that registers the machine for Remote Control in the Antigravity
  app -- an operator surface, not a local injection API;
* `-p/--prompt`, `-i/--prompt-interactive` and `--conversation <id>` each start
  a new run, not an injection into the live one;
* the supported extension surface is hooks (`PreInvocation`, `Stop`).

So an Agy seat is supervised by design. `atm join --persistent` refuses
with that reason instead of registering an endpoint, `atm msg` prints
`supervised (agy has no live-session injection; mail waits for its next hook or
persist run)` instead of a fake wake, and mail is delivered by
`atm hooks agy` (PreInvocation/Stop) or a persist watcher.

If a future `agy` build ships a local session API, add it to
`session_adapters.SUPERVISED_HARNESSES` removal + a probe, with the same rule:
`woken` only with evidence a turn started.
