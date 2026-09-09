# Author and update knowledge

Start by searching. This prevents another agent from recording the same diagnosis under a new name.

```sh
tickets knowledge query "component failure or decision"
tickets knowledge list --tag gliner
```

Create a node or edge as JSON outside the graph, then add it through the CLI. The CLI validates required provenance, timestamps, confidence, staleness, node types, edge types, IDs, and graph references before keeping the write.

```sh
tickets knowledge add /tmp/new-fact.json
tickets knowledge validate
git add knowledge/
git commit -m "Record verified runtime fact"
```

To correct an existing record, keep its stable `id`, update the source and `last_verified_at`, then run:

```sh
tickets knowledge update /tmp/corrected-fact.json
```

Node revision increments automatically. Git retains the previous version. Use `supersedes` when a new decision, artifact, or runbook replaces another record while both remain useful history.

Verification means:

| value | use |
|---|---|
| `unverified` | reported with no inspected evidence |
| `inferred` | reasoned from sources, not directly observed |
| `observed` | directly seen once |
| `verified` | source and claim checked together |
| `superseded` | retained history; always rendered stale |

Never put secrets, raw prompts, customer text, credentials, or full model outputs in a node. Record a privacy-safe summary and a source reference. Tickets may include `knowledge:<id>` so the prompt renderer can inherit a fact, but `.tickets/` is not graph storage.

Attach a reference without copying the fact into the ticket:

```sh
tickets brief --ticket T-123 --knowledge failure.gliner-cpu-ort-shadow
```

The ticket note stores only `knowledge:failure.gliner-cpu-ort-shadow`. The selected node, its provenance, and its related runbook still come from `knowledge/`.
