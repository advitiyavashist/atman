# Published take evidence

These files restore the provider receipts that survived after the original
temporary run directory was deleted. They belong to the completed take in PR
#211 at `bac8b4ad1d8af0a0c64f30430e80b04ab5a52b14`.

The three Codex excerpts retain the take's user prompt, assistant messages,
tool calls and tool results. The Cursor excerpt retains the recorded provider
transcript, including its own `atm next`, the accepted-SHA check, the
`git merge --ff-only` call, code edits, tests and final commit. They exclude
system/developer prompt boilerplate. Local paths and the local account name
were replaced with `<RUN>`, `<HOME>`, `<TMP>` and `<operator>`; each original
source digest and sanitized-excerpt digest is in `manifest.json`.

This is execution evidence, not tamper-proof attestation. It proves that the
provider records contain the displayed actions and results. GitHub PR #5 still
resolves to accepted commit
`db0849bb7c3928e0123b6007ff6fb8b1a23d352d`. The Cursor transcript records an
ff-only merge of that SHA before commit
`ba286156e85d715815ec90bd18d372bbfde55e8a`. That B commit was never pushed and
the temporary worktree no longer exists, so its object ancestry cannot now be
checked independently. The public caption describes the recorded execution;
it is not an independently replayable ancestry claim.

The cast is a presentation surface, not a raw transcript: each provider command
printed only its final lines. The receipts therefore contain more history than
the cast, including the planner's initial reservation identity failure and the
Codex worker's rejected stale-branch push and merge-conflict recovery. The final
unique branch, PR, independent review, acceptance and dependent implementation
all follow those retries. No permission-denial event appears in the preserved
provider records.

`prompts/` contains the four actual prompts from the provider records with the
temporary root replaced by `<RUN>`. To prepare a fresh disposable run:

```sh
python3 docs/demo/rehearsal.py setup --mode real --origin <empty-throwaway-origin>
python3 docs/assets/demo/prepare-replay.py <RUN>
zsh docs/assets/demo/record-take.sh <RUN>
```

The corrected replay script preserves the original ten command lines and uses
the corrected narration. A replay creates new provider output; it does not
recreate or overwrite the published cast.
