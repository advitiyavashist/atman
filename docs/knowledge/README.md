---
id: knowledge
title: Team knowledge
tags: [knowledge, index, onboarding]
---

# Team knowledge

**Standing files. Not a memory product.**

## KB v0 lock (CEO / PM)

**KB v0 = board docs + tracked docs + `.tickets/briefs/` (`_shared` /
`roles/<role>` / `<agent>`) — the same E-013 inject contract.**

| Slice | Where | How a seat sees it |
|---|---|---|
| Board docs | `.tickets/MASTER.md`, `CONTEXT.md`, repo `HANDOFF.md` | `tickets master`, `tickets context`, claim briefing |
| Tracked docs | `docs/knowledge/` (this tree) and other committed guides | `tickets knowledge` / `show`, or open the file |
| Briefs | `.tickets/briefs/_shared.md`, `roles/<role>.md`, `<agent>.md` | watch / spawn / `tickets prompt` (E-013) |

That is the whole product. Standing context reaches a prompt **only**
through those brief files. Tracked docs are the catalog. Board docs are
the live mission.

**Not v0 (do not build):**

- A shared-memory brain / latent mind / “what we learned Tuesday” store
- A vector DB, embeddings, or graph-doc index
- Auto-sync from this tree into role briefs (no `knowledge pin`, no
  tag→role dump, no second inject root)

`$TICKETS_KNOWLEDGE_DIR` overrides the tree (tests). There is no
`.tickets/knowledge/` store.

| If you are… | Read |
|---|---|
| Adding a doc, tagging it, how seats see it | [howto.md](howto.md) |
| What is in vs out of KB v0 | [kb-lock.md](kb-lock.md) |
| How text reaches a prompt | [inject.md](inject.md) |

Index (also `tickets knowledge`):

| id | Title | Tags |
|---|---|---|
| knowledge | This page | knowledge, index, onboarding |
| howto | Add a doc, tag it, how seats see it | howto, operator |
| kb-lock | KB v0 lock | knowledge, product, master |
| inject | E-013 inject contract | inject, briefs |

```sh
tickets knowledge                 # list tracked docs (optional --tag backend)
tickets knowledge show kb-lock
tickets brief --role backend "…"  # standing inject — same as E-013
tickets brief --role backend --show
tickets prompt --agent alice
```
