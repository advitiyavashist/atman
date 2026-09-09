# Shared knowledge

Atman keeps durable knowledge separate from work coordination.

- `.tickets/` answers who is doing what, what is blocked, and what is ready.
- `knowledge/` answers what the team knows, why it believes it, where the evidence lives, whether it is stale, and which runbook or skill applies next.

The knowledge layer is a repo-backed entity/relation/evidence graph. It is local-first JSON so every harness can read the same reviewed bytes and Git provides history. It does not require a vector database or hosted service.

```sh
tickets knowledge validate
tickets knowledge list --type failure
tickets knowledge query "GLiNER T4 latency" --agent "$TICKET_AGENT"
tickets knowledge show failure.gliner-cpu-ort-shadow
```

`tickets prompt`, `tickets watch`, and `tickets spawn` use one renderer. It selects a compact subgraph from the held ticket, role, capabilities, harness, and explicit `knowledge:<id>` references. The default budget is 3,600 characters and the hard ceiling is 6,000. Full evidence stays in the source artifact.

When the board and knowledge repository are different checkouts, register the canonical graph once per seat:

```sh
tickets join alice --roles backend --harness claude \
  --knowledge-dir /path/to/atman/knowledge
```

The board stores only that path reference in the seat record. The graph bytes remain in the knowledge repository. `ATMAN_KNOWLEDGE_DIR` is the process-level override for CI and temporary environments.

Read [howto.md](howto.md) to add or update facts, [schema.md](schema.md) for the node and edge contract, and [inject.md](inject.md) for selection and prompt behavior.
