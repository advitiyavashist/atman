# Hook identities

Each installed hook owns one agent identity. The installer writes that agent
and the board path into the command, so an old `TICKET_AGENT` or `TICKETS_DIR`
in the launching shell cannot change who receives or posts board work.

Use one settings file or hook file per harness/worktree:

```sh
tickets hooks claude --agent claude-api --settings "$PWD/.claude/settings.json"
tickets hooks codex --agent codex-api --worktree "$PWD" --hooks-file "$HOME/.codex/hooks.json"
tickets hooks cursor --agent cursor-api --worktree "$PWD"
```

Every SessionStart, inbox, Stop, and task-wake path first runs the durable
`tickets identity` operation under the baked identity. Hook files are written
atomically. The latest changed install stores an adjacent mode-0600 rollback
receipt. An idempotent reinstall does not replace that receipt.

To restore the exact bytes that preceded the latest install:

```sh
tickets hooks claude --agent claude-api --settings "$PWD/.claude/settings.json" --rollback
tickets hooks codex --agent codex-api --hooks-file "$HOME/.codex/hooks.json" --rollback
tickets hooks cursor --agent cursor-api --worktree "$PWD" --rollback
```

Rollback refuses when the installed target has changed since installation.
This prevents a stale receipt from erasing a later edit.

## Generic and remote agents

`remote` creates an executable identity-pinned wrapper and a JSON command
manifest beside it. With no arguments, the wrapper prints the task-wake prompt.
With arguments, it runs the normal ticket CLI under that one identity.

```sh
tickets hooks remote --agent remote-worker \
  --wrapper "$HOME/.local/bin/tickets-remote-worker"

tickets-remote-worker identity
tickets-remote-worker inbox
tickets-remote-worker msg "started" --re T-123
```

The manifest contains ready-to-register commands for `SessionStart`, `inbox`,
`Stop`, and `taskWake`. Each command includes `--agent remote-worker` and the
absolute board path.

For the persistent Grok chief-of-staff seat:

```sh
tickets hooks remote --agent grok-worker \
  --prompt-kind cos \
  --wrapper "$HOME/.local/bin/tickets-grok-worker"
```

The remote session should call `tickets-grok-worker` at startup and whenever
the message-board wake fires. It can then use `tickets-grok-worker <command>`
for all board work. A separate terminal may remain `TICKET_AGENT=cto`; the
wrapper will still verify and use `grok-worker`.

