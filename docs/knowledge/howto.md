---
id: howto
title: Add a doc, tag it, how seats see it
tags: [howto, operator]
seats: [master]
---

# Add a doc, tag it, how seats see it

Operator path. No new store. The file you write here is the source of
truth; the brief is only a pointer.

## 1. Add a doc

Create a markdown file under `docs/knowledge/` (one extra folder is fine:
`docs/knowledge/ops/oncall.md`). Frontmatter is required for a stable id
and tags:

```markdown
---
id: oncall
title: Who to wake
tags: [ops, master]
seats: [master]
---

# Who to wake

…
```

- `id` — slug seats and `tickets knowledge show` use. `[A-Za-z0-9][A-Za-z0-9_.-]*`
- `title` — one line for the index
- `tags` — comma or `[a, b]` list. Filter with `tickets knowledge --tag ops`
- `seats` — hint only (who this is *for*). Inject still follows **roles**,
  not this field. Pin the doc to the lane that should see it.
- `pin` — optional one-line excerpt used when pinning (otherwise the first
  paragraph is used, capped)

Commit the file. The tree is repo-tracked on purpose: team knowledge
survives a board clear. Briefs are the standing inject; this tree is the
catalog.

## 2. Tag it

Tags are the index. Prefer few, stable words: `backend`, `docs`, `ops`,
`product`, `master`, `inject`. Do not invent a taxonomy product.

```sh
tickets knowledge --tag ops
```

`rg` over this folder works too. There is no graph and no embedding step.

## 3. How seats see it

Seats do **not** get the whole tree on every wake. Context is the bill.

| Path | What the seat sees | When to use it |
|---|---|---|
| `tickets knowledge` / `show <id>` | Index or one doc, on demand | The seat knows it needs a fact |
| `tickets knowledge pin <id> --role <lane>` | A short pointer inside `.tickets/briefs/roles/<lane>.md` | Standing lane knowledge |
| `pin <id> --shared` | Pointer in `.tickets/briefs/_shared.md` | Every seat, keep it short |
| `pin <id> --agent <name>` | Pointer in `.tickets/briefs/<name>.md` | One worker |
| `tickets brief --role <lane> --file …` | You wrote the brief yourself | When a pointer is not enough |

Watch / spawn / `tickets prompt` inject those brief files in the order
already documented in the [master how-to](../onboarding/master-howto.md)
(E-013). A pin is an append to that file. A missing pin is silence, not a
fallback to this tree.

Check what inject will see (run bare):

```sh
tickets knowledge pin memory --role backend
tickets brief --role backend --show
tickets prompt --agent alice          # after alice joined --roles backend
```

Unpin by editing the brief (delete the `Knowledge [id]:` line). There is
no `knowledge unpin` and no latent copy.

## What not to do

- Do not dump the catalog into `_shared.md`.
- Do not create `.tickets/knowledge/` or a second inject root.
- Do not add this tree to the three-file master onboarding path
  (`MASTER.md`, `HANDOFF.md`, `tickets map`). Pull a doc when the work
  needs it; do not grow the first-read budget.
