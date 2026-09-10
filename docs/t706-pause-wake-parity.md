# T-706 pause→wake poke parity

Gold: a **paused** connected session takes the next turn from `tickets msg --to <seat>`
without the seat polling `tickets pending`. Claude AF_UNIX inject is the proven bar.

Poll-based retrieval (`tickets watch` / inbox on the next cron) is **not** a PASS.

## Matrix

| Provider | Register | Poke command | Pause→resume | Result |
|---|---|---|---|---|
| Claude | `join --persistent` with `CLAUDE_CODE_MESSAGING_SOCKET` | AF_UNIX inbox (`auth` JSON + text newline) | inject into paused Claude Code session | **PASS** (regression: `test_claude_socket_regression`) |
| Codex | native when app-server sock live | `codex queue` then `thread/queue/start` / `turn/start` via `codex app-server proxy` | **PASS** `woken`; sqlite-only is `queued-offline` not PASS |
| Cursor / Grok Bot | persist+tmux | `tmux send-keys` into paused `agent persist` | **PASS** `woken` |
| Cursor / Grok Bot | live ACP control sock + `CURSOR_CONVERSATION_ID` | `session/load` then `session/prompt` (never spawn `agent acp` / `-p --resume`) | **PASS** `woken` |
| Cursor / Grok Bot | conversation id only | none (`agent -p --resume` is a new paid run) | **FAIL-CLOSED** supervised |
| Grok remote | schema-2 bridge (`tickets remote register`) | T-640 `remote next` claim | reconnect delivers queued work | protocol only; no Grok binary on this host |
| Grok native | n/a | n/a | n/a | **FAIL-CLOSED** (`remote bridge required`) |

## Root cause vs Claude

Claude's messaging socket is a live interrupt into the already-connected session.
`codex queue --thread` only appends a mailbox item (`thread/queue/add` without
`thread/queue/start` / `turn/start`), so a paused Codex seat can still look
`wake queued` on the board while the human/TUI later retrieves via poll
(`watcher_woke=no`). Cursor `agent -p --resume` starts a different paid process
and is not pause-resume.

## Operator setup

```sh
# Codex persistent seat (ml-eng): keep the app-server daemon up so pokes resume.
codex app-server daemon start   # creates ~/.codex/app-server-control/app-server-control.sock
tickets join ml-eng --persistent --harness codex --wake-mode continuous --lifecycle persistent

# Cursor / Grok Bot: persist in tmux, then join from that session.
agent persist "…"               # requires tmux
# inside the persist session:
export CURSOR_PERSIST_SESSION="$(tmux display-message -p '#{session_name}')"
tickets join <seat> --persistent --harness cursor --wake-mode continuous --lifecycle persistent

# Or: long-lived ACP proxy (Codex app-server analog). Do not spawn per poke.
python3 scripts/cursor_acp_proxy.py   # ~/.cursor/acp-control/acp-control.sock
# from the interactive Cursor session (CURSOR_CONVERSATION_ID already set):
tickets join <seat> --persistent --harness cursor --wake-mode continuous --lifecycle persistent
```

`tickets msg --to <seat>` prints `wake: <seat> -> woken` on a true resume.
`poll-only` and `supervised` are durable queued work, not native PASS.
