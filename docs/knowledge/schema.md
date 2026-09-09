# Knowledge graph schema

`knowledge/manifest.json` declares schema version and prompt budget. Records live one per file under `knowledge/nodes/` and `knowledge/edges/`, which reduces merge conflicts between agents.

Every node requires:

- `kind: "node"`, stable `id`, `type`, `title`, and compact `summary`;
- `tags` and `applies_to` arrays;
- `source.ref`, `recorded_at`, `owner`, and `last_verified_at`;
- `verification`, confidence from 0 to 1, `stale_after_days`, and integer `revision`;
- optional structured `data` and a safe stable-slug `canonical_key`.

Node types are `project`, `component`, `decision`, `artifact`, `model_pin`, `experiment`, `failure`, `runbook`, `skill`, and `agent_capability`.

Every edge requires the same provenance, verification, confidence, and staleness fields plus `kind: "edge"`, stable `id`, `from`, `to`, and `type`. Both endpoint nodes must exist.

Edge types are `depends_on`, `supersedes`, `produced_by`, `failed_because`, `verified_by`, `applies_to`, and `requires`.

Staleness is computed from `last_verified_at + stale_after_days`. A null policy means the fact has no time-based expiry; `superseded` records are always stale. Query deduplication chooses the strongest current node for each `canonical_key` by verification state, revision, verification time, then confidence.

Timestamps more than five minutes ahead of the local clock are rejected so an accidental future date cannot suppress staleness. Graph JSON files may not resolve through a symlink outside the graph root. Ticket references name nodes only and are parsed as exact `knowledge:<id>` tokens.
