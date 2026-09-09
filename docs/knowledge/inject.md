# Compact inheritance

Atman derives query terms from the current seat and held work:

- ticket ID, title, body, role, needs, and attached context;
- agent roles, capabilities, and harness;
- explicit `knowledge:<id>` references.

It ranks matching nodes, follows one relation hop so evidence can bring its failure or runbook, deduplicates by `canonical_key`, labels stale records, and renders only summaries plus source references. `knowledge/manifest.json` sets the normal character budget. Code enforces a 6,000-character ceiling.

The result enters `prompt_text`, the common prompt-file contract used by Claude, Codex, Cursor, Grok, and custom harnesses through `tickets prompt`, `watch`, and `spawn`. Claude's `tickets board` SessionStart hook, Codex's native context hook, and Cursor's generated hook also request the same bounded selector. Harness-specific code does not rank knowledge.

Resolution order is `ATMAN_KNOWLEDGE_DIR`, the seat's `tickets join --knowledge-dir`, then `knowledge/` in the current worktree. This lets agents share one graph across project boards without copying it into `.tickets/`.

Briefs remain operator-written standing instructions. Knowledge inheritance is task-selected evidence. Both appear in the prompt, from different stores, for different purposes.

Inspect the exact output without starting a model:

```sh
tickets prompt --agent alice
tickets knowledge query "GLiNER CUDA" --agent alice --max-chars 1800
tickets knowledge query "knowledge:failure.gliner-cpu-ort-shadow"
```

An invalid graph fails closed with a short instruction to run `tickets knowledge validate`; malformed graph content is never injected.
