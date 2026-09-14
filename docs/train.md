# Review-and-integrate train

Stack several ready PRs onto one integration branch, verify the stack once
against main, then land each PR at the exact SHA that was verified.

```sh
tickets train build --prs 137,116,129
# or already-fetched local refs:
tickets train build --refs pr-137,pr-116 --artifact /path/to/repo

# Compare hermetic full-suite failure ids (one nodeid per line):
tickets train verify --main-failures main-fails.txt --train-failures train-fails.txt

# Named merge executor only, after ACCEPT, at the built repo:
tickets train land --artifact /path/to/repo
```

`atm` is the same CLI as `tickets`.

## Rules

- `build` uses `git merge --no-ff` in the given order. The first conflict
  stops the train and names the pair (`conflict: A then B`). It never
  deletes an existing branch; a preexisting train branch is refused.
- `verify` ACCEPTS only when both logs include a completed-run marker
  (`# suite-ran` or `# suite-ran=true`) and the train introduces no failure
  ids that main does not already have. `# suite-ran=false`,
  `# not-suite-ran`, aborted/timed-out tokens, and empty logs stay
  `UNKNOWN`. ACCEPT writes a source/env/digest evidence receipt.
- `land` calls `gh pr merge <n> --match-head-commit <sha>` bound to the
  artifact (`--repo owner/name` when origin is GitHub, always `cwd` of
  `--artifact`). It refuses unless `train.json` has the full schema
  (`repo`, `main_sha`, `train_sha`, `strategy=no-ff`), verdict is `ACCEPT`
  (or `GO-WITH-DEBT` with `policy=go-with-debt` plus named
  `debt_authority` and `debt_baseline`), the caller is the named merge
  executor, `--artifact` is the receipt repo, the recorded train SHA still
  matches, and both local trunk and `origin/<trunk>` still match the
  verified base. Incomplete or legacy receipts must be rebuilt.
- Recorded fields: train SHA, main SHA, member SHAs, repo bind, strategy,
  verdict, evidence.

This does not replace `tickets merge` for a single pinned review SHA. It is
the batch path used when several PRs should share one hermetic suite run
(T-919).
