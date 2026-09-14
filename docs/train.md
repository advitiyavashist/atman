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
- `verify` ACCEPTS only when both logs include `suite-ran` evidence and the
  train introduces no failure ids that main does not already have. Empty or
  missing lists without that mark stay `UNKNOWN`.
- `land` calls `gh pr merge <n> --match-head-commit <sha>` per member. It
  refuses unless `train.json` says `verdict=ACCEPT` (or `GO-WITH-DEBT` with
  an explicit `policy=go-with-debt` receipt), the caller is the named merge
  executor, `--artifact` is the receipt repo, and the recorded train SHA /
  trunk SHA / no-ff strategy still match the tree.
- Recorded fields: train SHA, main SHA, member SHAs, repo bind, strategy,
  verdict.

This does not replace `tickets merge` for a single pinned review SHA. It is
the batch path used when several PRs should share one hermetic suite run
(T-919).
