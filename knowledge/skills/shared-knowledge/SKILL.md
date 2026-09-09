---
name: shared-knowledge
description: Reuse verified project facts, experiment failures, model pins, runbooks, and task skills before repeating investigation or execution across agent harnesses.
---

# Shared knowledge

Use this skill when starting work that may depend on an earlier decision, model or artifact pin, experiment, failure, or runbook.

Query before investigating or running:

```sh
tickets knowledge query "<project component task>" --agent "$TICKET_AGENT"
```

If a ticket names `knowledge:<id>`, query or show that exact record. Read its `source.ref` before changing a verified fact. Treat `STALE` as a prompt to revalidate, not as current evidence.

Keep coordination in tickets and durable findings in the knowledge graph. A ticket may reference a knowledge ID; ticket notes are not the fact store.

When a run fails, record the exact job ID or command, error, root cause, source, and the corrected next command. Link the failure and runbook rather than copying the same diagnosis into new ticket briefs. Never turn an inferred cause into `verified` evidence.

When updating a fact, preserve its stable ID, provide a newer `last_verified_at`, and run:

```sh
tickets knowledge validate
tickets knowledge query "<affected task>" --max-chars 3600
```

Prompt inheritance is deliberately small. Open the referenced record or source when full detail is needed.
