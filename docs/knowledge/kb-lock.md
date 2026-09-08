---
id: kb-lock
title: KB v0 lock
tags: [knowledge, product, master]
---

# KB v0 lock

**Standing files. Not a memory product.**

The team shares **files the next reader can open** — board docs, tracked
docs, and briefs. That is KB v0. Nothing else.

## In (KB v0)

| Slice | Where |
|---|---|
| Board docs | `.tickets/MASTER.md`, optional `CONTEXT.md`, repo `HANDOFF.md` |
| Tracked docs | `docs/knowledge/` and other committed guides |
| Briefs | `.tickets/briefs/_shared.md`, `roles/<role>.md`, `<agent>.md` |

Inject is E-013: watch / spawn / `tickets prompt` read those brief
paths and nowhere else. Repo-root `roles/` is a template. This tree is
a catalog. Missing brief = no block.

Trajectories and harness session state stay what they were (turns/cost;
the runner's own context). They are not a knowledge product and they
are not a second inject root.

## Out

- Shared-memory brain, latent "what we learned Tuesday"
- Vector DB, embeddings, graph-doc product
- Auto-sync role KB (copying this tree into briefs on a schedule or by tag)
- Modal, or any remote memory service
- A fourth file on the master onboarding path (`MASTER.md`, `HANDOFF.md`,
  `tickets map`)

Silence means the operator did not write the brief and the seat did not
open a tracked doc. That is honest.
