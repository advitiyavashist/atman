# Connect Antigravity (`agy`) to this board

Antigravity is a built-in harness (`agy`, alias `antigravity`). Same prompt
contract as Claude / Codex / Cursor: the runtime writes a prompt file, `agy`
runs headless, the board is the mailbox.

Do not use `/opt/homebrew/bin/gemini` as a BYOA seat. The binary is
`agy` (`~/.local/bin/agy`).

## 1. Join

```sh
tickets join "$TICKET_AGENT" --harness agy --roles backend --persist \
  --wake-mode continuous --lifecycle persistent
```

Headless spawn (unattended):

```sh
tickets spawn NAME --harness agy --persist --roles backend
# --safe uses `agy --mode accept-edits` instead of --dangerously-skip-permissions
```

An Agy seat is **supervised**: `agy` has no live-session injection, so board
mail is delivered by the hooks below (or a persist watcher), and a wake receipt
says `supervised (...)` rather than `woken`. `tickets join --persistent` will
tell you the same. Evidence: docs/wake-recipients.md.

## 2. Hooks

```sh
tickets hooks agy --agent "$TICKET_AGENT"
```

Writes project `.agents/hooks.json`:

- `PreInvocation` → `tickets hook-run --event agy-inbox` (inject unread mail)
- `Stop` → `tickets hook-run --event agy-stop` (`block` mapped to `continue`
  so Antigravity does not treat board-still-busy as a hard stop)

Open a **new** session after install so hooks load. Spawn copies `.agents`
into the worktree.

## 3. Mail

The bus is `tickets msg` / `tickets inbox`. If native inject has no live
endpoint, a live persist watcher is poked (`wake: name -> watch-poked`).
Spawn the watcher with this `tickets.py`, not a stale PATH shim from another project.
