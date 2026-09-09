# Product boundary

Atman manages multi-agent work while preserving separability. Models remain independent seats with their own sessions, permissions, worktrees, and context windows. The knowledge graph gives each seat reviewed context relevant to its current task.

The ticket board and knowledge graph are separate:

| system | owns | does not own |
|---|---|---|
| Ticket board | assignment, dependencies, status, messages, liveness | durable technical truth |
| Knowledge graph | decisions, pins, evidence, failures, runbooks, skills | assignment or agent lifecycle |

A ticket can reference `knowledge:<id>`. Closing or clearing tickets does not delete the referenced record. A knowledge entry cannot claim, unblock, merge, or close a ticket.

V1 is repo-backed JSON with deterministic lexical search and typed edges. Add embeddings, an external database, or learned ranking only after measured scale or retrieval quality shows the local design is insufficient.
