---
id: howto
title: Add a doc, tag it, how seats see it
tags: [howto, operator]
---

# Add a doc, tag it, how seats see it

Operator path for KB v0. No new store. No auto-sync into role briefs.

## 1. Add a tracked doc

Create markdown under `docs/knowledge/` (one extra folder is fine:
`docs/knowledge/ops/oncall.md`). Frontmatter gives a stable id and tags:

```markdown
---
id: oncall
title: Who to wake
tags: [ops, master]
---

# Who to wake

…
```

- `id` — slug for `tickets knowledge show`. `[A-Za-z0-9][A-Za-z0-9_.-]*`
- `title` — one line for the index
- `tags` — comma or `[a, b]` list. Filter with `tickets knowledge --tag ops`.
  Tags are an index, not a routing table.

Commit the file. Tracked docs survive a board clear. Board docs and
briefs are the other two slices of KB v0 — see [kb-lock.md](kb-lock.md).

## 2. Tag it

Prefer few, stable words: `backend`, `docs`, `ops`, `product`, `master`.
Do not invent a taxonomy product. Do not embed.

```sh
tickets knowledge --tag ops
```

`rg` over this folder works too.

## 3. How seats see it

Seats do **not** get the whole tree on every wake. Context is the bill.

| Path | What the seat sees | When to use it |
|---|---|---|
| `tickets knowledge` / `show <id>` | Index or one tracked doc, on demand | Occasional fact |
| Open the markdown | Same file | Same |
| `tickets brief --role <lane> "…"` or `--file` | Standing text in `.tickets/briefs/roles/<lane>.md` | Lane must see it every wake |
| Edit `.tickets/briefs/_shared.md` | Every seat (E-013) | House rules; keep it short |
| `tickets brief <agent> "…"` | `.tickets/briefs/<agent>.md` | One worker |

Watch / spawn / `tickets prompt` inject **only** those brief files
([master how-to](../onboarding/master-howto.md), E-013). A tracked doc
is not a fallback. Tags do not auto-copy into a role brief.

Check what inject will see (run bare):

```sh
tickets brief --role backend "One file per change. Do not hold review."
tickets brief --role backend --show
tickets prompt --agent alice          # after alice joined --roles backend
```

## What not to do

- Do not dump the catalog into `_shared.md`.
- Do not create `.tickets/knowledge/` or a second inject root.
- Do not auto-sync this tree into `briefs/roles/` by tag or schedule.
- Do not add a vector DB or embedding step.
- Do not add this tree to the three-file master onboarding path
  (`MASTER.md`, `HANDOFF.md`, `tickets map`).
