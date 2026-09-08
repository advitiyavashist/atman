---
id: knowledge
title: Team knowledge
tags: [knowledge, index, onboarding]
seats: [master]
---

# Team knowledge

Atman is the seat. Brahman is the whole that seat works inside. This tree is
how a seat reads what the whole already wrote.

It is **team docs**: searchable markdown, tagged, pin-able into the brief
that watch and spawn already inject. It is **not** a shared-memory brain, a
graph-doc product, or a latent Om store.

**Atman memory** (product lock) is still only:

1. Board docs — `MASTER.md`, `HANDOFF.md`, `CONTEXT.md`
2. Trajectories — `.tickets/trajectories.jsonl`
3. Harness context — what the runner already holds
4. Role briefs — `.tickets/briefs/` (E-013 inject)

This folder does not add a fifth memory substrate. Pinning a doc writes a
pointer into a brief. The next `tickets prompt` / watch / spawn sees it
because briefs already inject. Nothing else is stored.

| If you are… | Read |
|---|---|
| Adding or tagging a doc; how seats see it | [howto.md](howto.md) |
| What counts as memory vs this tree | [memory.md](memory.md) |
| How a doc reaches a prompt | [inject.md](inject.md) |

Index (also `tickets knowledge`):

| id | Title | Tags |
|---|---|---|
| knowledge | This page | knowledge, index, onboarding |
| howto | Add a doc, tag it, how seats see it | howto, operator |
| memory | Atman memory lock | memory, product, master |
| inject | How knowledge reaches a prompt | inject, briefs |

```sh
tickets knowledge                 # list (optional --tag backend)
tickets knowledge show memory
tickets knowledge pin memory --role backend   # pointer into the existing brief
```

`$TICKETS_KNOWLEDGE_DIR` overrides the tree (tests). Default is
`docs/knowledge/` at the repo / worktree root. There is no
`.tickets/knowledge/` store.
