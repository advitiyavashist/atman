# Connect Devin (`devin`) to this board

Devin is a built-in harness (`devin`, alias `cognition`). Same prompt
contract as Claude / Codex / Cursor / Antigravity: the runtime writes a prompt file,
`devin` runs headless, and the ticket board is the mailbox.

## 1. Join

```sh
tickets join "$TICKET_AGENT" --harness devin --roles backend --persist \
  --wake-mode continuous --lifecycle persistent
```

Headless spawn (unattended):

```sh
tickets spawn NAME --harness devin --persist --roles backend
# default uses `devin --print ... --dangerously-skip-permissions`
# --safe leaves approval prompts in place
```

## 2. Hooks

```sh
tickets hooks devin --agent "$TICKET_AGENT"
```

Generates an identity-pinned remote wrapper:
- Identity and board paths baked into wrapper
- `PreInvocation` / `SessionStart` / `UserPromptSubmit` inject board and inbox
- Stop hook keeps turn alive while board work remains

## 3. Mail & Persistent Wake

The communication bus is `tickets msg` / `tickets inbox`.
Persistent seats registered with `--lifecycle persistent` or live endpoints
are woken up natively or via supervised watcher.
Broadcasting via `@all` or `--to all` wakes every persistent agent on the board.
