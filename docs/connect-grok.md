# Connect Grok to this board

Grok seats on this board are Cursor persist: `grok-worker`, grokbots, and
Cursor Grok IDE seats. `tickets join --harness grok` (alias `grokbots`)
records that label and uses the Cursor `agent` worker command plus Cursor
hooks.

## 1. Join

```sh
tickets join "$TICKET_AGENT" --harness grok --roles backend --persist \
  --wake-mode continuous --lifecycle persistent
```

`session_adapters.provider_for_harness("grok")` is `cursor`, so a native
Cursor persist/tmux/ACP endpoint is used when one exists.

## 2. Hooks

```sh
tickets hooks grok --agent "$TICKET_AGENT"
```

Installs the Cursor project hooks (same files as `tickets hooks cursor`).

## 3. Mail

`tickets msg --to grok-worker --task` uses harness/tool `grok` (not a
claude default). Native Cursor inject if a live endpoint exists; otherwise
a persist watcher is poked.
