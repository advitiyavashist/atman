---
id: inject
title: E-013 inject contract
tags: [inject, briefs]
---

# How knowledge reaches a prompt

One renderer. Watch, spawn, and `tickets prompt` already share it. KB v0
**is** that path. It does not add a second inject root.

## Standing inject (E-013 — unchanged)

From the [master how-to](../onboarding/master-howto.md):

1. `.tickets/briefs/_shared.md`
2. `.tickets/briefs/roles/<role>.md` for each of the seat's roles
3. Workers only: `.tickets/briefs/<agent>.md`, then ticket context notes

Write those files with `tickets brief` (agent / `--role` / `--ticket`)
or by editing `_shared.md`. `--file` replaces; a bare line appends.

Repo-root `roles/` and `docs/knowledge/` are **not** read on inject.
Missing file = no block. Tags on a tracked doc do not choose a lane.

## Tracked docs (catalog, not inject)

```sh
tickets knowledge --tag backend
tickets knowledge show inject
```

The body stays out of the prompt until someone opens it, or until an
operator copies what must stand into a brief.

## Check

```sh
tickets brief --role backend "House rule: one file per change."
tickets brief --role backend --show
tickets prompt --agent alice          # after alice joined --roles backend
```

If the line is in `.tickets/briefs/roles/backend.md` and in the prompt,
E-013 is wired. If it lives only under `docs/knowledge/`, it is catalog.
