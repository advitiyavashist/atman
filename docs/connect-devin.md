# Connect Devin (`devin`) to this board

Devin is a built-in harness (`devin`, alias `cognition`). Same prompt
contract: the runtime writes a prompt file, `devin` runs headless, and the
ticket board is the mailbox. There is no native Devin adapter; persist
watch + the remote hook wrapper is the wake path.

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

Generates an identity-pinned remote wrapper (same schema as `tickets hooks remote`).

## 3. Mail

`tickets msg --to SEAT --task` posts to the board, then native wake. With no
live endpoint, a live persist watcher is poked (`watch-poked`). Do not spawn
a live Devin network from tests.
