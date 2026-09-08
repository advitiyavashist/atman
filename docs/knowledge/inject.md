---
id: inject
title: How knowledge reaches a prompt
tags: [inject, briefs]
seats: [master, backend, docs]
pin: Knowledge reaches a prompt only through the existing brief inject path. Pin a doc; do not add a second store.
---

# How knowledge reaches a prompt

One renderer. Watch, spawn, and `tickets prompt` already share it. Team
knowledge **extends that path**. It does not add a second inject root.

## Standing inject (unchanged)

From the [master how-to](../onboarding/master-howto.md):

1. `.tickets/briefs/_shared.md`
2. `.tickets/briefs/roles/<role>.md` for each of the seat's roles
3. Workers only: `.tickets/briefs/<agent>.md`, then ticket context notes

Repo-root `roles/` is still a template. This tree is also **not** read on
inject. Missing file = no block. That honesty is load-bearing.

## Pulling a knowledge doc in

**On demand (seat reads):**

```sh
tickets knowledge --tag backend
tickets knowledge show inject
```

The body stays out of the prompt until someone opens it. Prefer this
when the fact is occasional.

**Standing (operator pins into a brief):**

```sh
tickets knowledge pin inject --role backend
tickets knowledge pin memory --shared          # every seat; keep the excerpt short
tickets knowledge pin howto --agent scribe
```

`pin` appends one `Knowledge [<id>]:` line (plus a short excerpt) to the
same file T-529 / T-530 already inject. The next wake sees it because
`role_context` / `agent_brief` read that file. Idempotent: a second pin
of the same id is a no-op.

There is no `.tickets/knowledge/`, no `$TICKETS_ROLES_DIR` fallback, and
no automatic "all tags matching this role" dump. If you need the full
doc in the brief, paste it with `tickets brief --role <lane> --file`
yourself and own the context cost.

## Check

```sh
tickets brief --role backend --show
tickets prompt --agent alice
```

If the `Knowledge [inject]:` line is in the brief and in the prompt, the
path is wired. If it is only in `docs/knowledge/`, it is catalog, not
inject.
