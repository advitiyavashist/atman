---
id: memory
title: Atman memory lock
tags: [memory, product, master]
seats: [master]
pin: Atman memory is board docs, trajectories, harness context, and role briefs — not this tree, not a shared mind.
---

# Atman memory lock

Atman remembers as a team, not as a mind. The seat (Atman) and the whole
(Brahman) share **files the next reader can open**, not a hidden state
that accumulated while someone was talking.

## What counts as memory

| Surface | Where | What it holds |
|---|---|---|
| Board docs | `.tickets/MASTER.md`, repo `HANDOFF.md`, optional `CONTEXT.md` | Mission, current state, decisions, what is true about the work |
| Trajectories | `.tickets/trajectories.jsonl` | Turns, costs, outcomes — ids and counts, never prompt text |
| Harness context | The runner's own session | Whatever that harness already kept; the board does not scrape it |
| Role briefs | `.tickets/briefs/_shared.md`, `roles/<role>.md`, `<agent>.md` | Standing markdown injected on watch/spawn (E-013) |

That list is closed. A new kind of "memory" is a product change, not a
folder you add.

## What this knowledge tree is

Searchable team docs. Operators write them, tag them, and **pin** a
relevant one into a brief when a lane should see it on the next wake.
Seats can also `tickets knowledge show <id>` during a turn.

The tree is Brahman in the ordinary sense: the whole's written house
knowledge. It is not Atman-as-substrate. Pinning copies a pointer into
the brief; it does not merge minds.

## What this is not

- A shared-memory product, Om brain, or latent store
- A graph-doc product (no nodes, edges, or embeddings)
- Modal, or any remote memory service
- A fourth file on the master onboarding path
- An automatic dump of every tagged doc into every prompt

Silence means the operator did not pin and the seat did not ask. That is
honest. Do not "fix" it by loading the catalog behind the seat's back.
