# T-522 — history rewrite (HOLD on public)

This document is the public-prep history cutover for `advitiyavashist/atman`.
It records the full-history residual scan, the replacement orphan snapshot,
CLEAN evidence after rewrite, and the exact force-push runbook.

**Visibility stays private.** Advitiya / CTO flip GitHub visibility in a
separate step after this cutover is done and they are satisfied. This work
does not change repo visibility and must not be treated as permission to
flip public.

Related: tip-only scrub already merged as PR #16 (`0bce0de`, T-362 / T-366).
That PR did **not** rewrite history. `docs/PUBLIC_PREP.md` is the short
checklist; this file is the evidence and operator runbook.

## Decision

Residuals are **dense** (operator home paths and deleted internals appear in
hundreds of commits). `git filter-repo` / BFG would need a large path+text
blocklist and would still leave a long, noisy history.

**Preferred cutover:** orphan-squash the current clean tip onto a
single-commit replacement `main`. The working tree is already the intended
public surface (PR #16 plus the small tip redactions in this change).

Replacement branch (pushed, do not merge): `cursor/t522-orphan-main-9059`

## Force-push risk (read before any `+main`)

Replacing `origin/main` is a **history rewrite**. It is not a normal merge.

- Every existing commit SHA on `main` becomes unreachable from `main`.
- Open PRs, review pins, `release_sha` / ancestry checks, and local clones
  that expect the old graph will break until they reset or re-clone.
- `git pull` on an existing clone is the wrong recovery. Operators must
  `fetch` + `reset --hard` (or re-clone).
- **Force-pushing only `main` is not enough for a public flip.** This repo
  has ~155 remote branches. Those refs still reach the dirty objects. If the
  repository is made public while they exist, the old history is public.
- GitHub keeps PR head/base commits and may serve them after the branch is
  deleted. Treat old SHAs as potentially recoverable even after the rewrite.
  Rotate any credential that ever lived in this repo, a fork, a worktree, or
  CI, regardless of scanner results.
- Do **not** run `git push --force` against `main` from a Cloud Agent VM
  unless Advitiya / CTO explicitly order it. The Mac operator (repo owner)
  owns the force-push.
- `--force-with-lease` is required so a newer `origin/main` is not clobbered.
- After cutover, keep the repo **private** until leftover branches/tags are
  gone and Advitiya / CTO choose to flip visibility.

## 1. Residual list (current published history)

Scan date: 2026-09-08. Object: all 524 local commits (`git rev-list --all`),
including `origin/main` at `d11236fd21c43434bf553c083183019a59285be4`
(361 commits on `main`) and ~155 remote branches. No values from matches
are reproduced here.

### Credential scanners

| Tool | Scope | Result |
|---|---|---|
| gitleaks 8.24.2 | git history (`297` commits on default walk; `--log-opts=--all` same) | **0 leaks** |
| trufflehog 3.88.27 | `git file://` full repo, verification off | **0 verified, 0 unverified** |
| filename walk | `.env`, `modal.toml`, `credentials.json`, `*.pem`, `*.key` as committed paths | **none** |
| PEM / `BEGIN PRIVATE KEY` content | all commits | **none** |
| DB URLs with embedded passwords | all commits | **none** |
| HuggingFace / AWS / Slack / GitHub token prefixes | all commits | **none live** |

OpenAI-like `sk-` hits: **494**, all in
`tests/fixtures/claude_hooks/prompt_with_secret.json`, classified
**placeholder** (`sk-live_aaaaaaaaaaaaaaaa` — used to prove the adapter
redacts). No raw secret is pasted here.

`modal.toml` / “modal token” hits: **189**, all the `.gitignore` line that
*ignores* `modal.toml`. Not a credential.

### Internals still reachable in old commits

These are the reason a tip scrub is insufficient. Counts are match
occurrences across commits (the same line in an old file is counted once
per commit that still contains it).

| Class | Occurrences | Commits | Notes |
|---|---|---|---|
| Absolute `/Users/<handle>/…` | 3535 | 513 | handles: operator home **3080**, test placeholder `someone` **455** |
| Deleted internal docs still in history | (file present) | see below | PR #16 removed them from the tip only |
| Steer / live-board / pin-table ops | 5257 | 467 | includes deleted docs **and** leftover tip files since scrubbed here |
| Seat / operator-path mentions | 3145 | 433 | mostly the same `/Users/<operator>` files |
| `.tickets` / `tickets.json` string | 8355 | 523 | **mostly product code and docs**, not a committed live board dump |

Deleted-from-tip files that **remain in history** (commit-count where the
path still existed):

| Path | Commits still containing it |
|---|---|
| `docs/HANDOFF.md` | 324 |
| `docs/implementation-plan.json` | 323 |
| `docs/messaging-plan.json` | 322 |
| `docs/INTEGRATION.md` | 301 |
| `docs/LIVE_CLI.md` | 88 |
| `docs/CROSS_REPO_PINS.md` | 69 |
| `docs/brand/ACCEPT.md` | 20 |

Historical files that contained a real `/Users/<operator>/` home path
(not the `/Users/someone` test fixture):

- `docs/HANDOFF.md`
- `docs/INTEGRATION.md`
- `docs/implementation-plan.json`
- `docs/messaging-plan.json`
- `docs/handoffs/reports/e010-artifact-map.md`
- `docs/runbook.md` (removed earlier)
- `scripts/make_sample_legacy_board.py`
- `src/ticket_board/adapters/claude/adapter.py` (historical default roots)
- `src/ticket_board/cli.py` / `tickets.py` (historical comments)
- assorted tests and adapter fixtures (T-210 later placeholderised the tip)

No committed live `.tickets/` board dump was found as a path. Live board
content leaked via **docs** (pin tables, scorecard provenance, HANDOFF).

### Tip leftovers found after PR #16 (fixed in this change)

PR #16 cleaned the tree as of `0bce0de`. Later merges reintroduced, or
left, a few internals on `origin/main` `d11236f`:

| Path | Issue | Action in this change |
|---|---|---|
| `docs/handoffs/reports/e010-artifact-map.md` | live review-queue dump, operator `/Users/…` paths, steer pins | deleted (same class as `CROSS_REPO_PINS.md`) |
| `docs/turns-scorecard-organic.md` | live board path `/Users/<operator>/Downloads/steer/.tickets` | path redacted |
| `tests/fixtures/t349_adversarial_trajectories.jsonl` | `pytest-of-<operator>` in captured tmp paths | renamed to `pytest-of-operator` |

Remaining `/Users/` strings on the tip are **intentional placeholders**
(` /Users/<operator> `, `/Users/someone` in an error-redaction test, and
the T-210 guard in `tests/test_contracts.py`).

## 2. Replacement snapshot

An orphan commit was built from the **current scrubbed tree** (this
branch), with no parents:

```
git commit-tree $(git rev-parse HEAD^{tree}) -m "…"
git branch cursor/t522-orphan-main-9059 <that-sha>
```

The orphan tree matches this branch’s tip. Product code, tests, and docs
are unchanged except for the redactions above plus this document.

Scan the orphan in a **fresh clone that contains only that branch**.
Scanning this working repo with `--all` will still see the dirty objects
until `main` is replaced **and** other refs are deleted.

## 3. CLEAN evidence (rewritten history)

Isolated re-scan of `cursor/t522-orphan-main-9059` on 2026-09-08.
Method: `git bundle create` of that ref only, then `git clone` the
bundle (543 objects / 1.01 MiB pack — not the 3832-object source pack).

| Check | Result |
|---|---|
| Reachable commits | **1** |
| gitleaks 8.24.2 | **0 leaks** (1 commit scanned, ~3.44 MB) |
| trufflehog 3.88.27 | **0 verified, 0 unverified** (722 chunks) |
| `kavana` string in reachable history | **absent** |
| `/Users/<real-handle>/` | **absent** (only `/Users/someone` test placeholder + this doc naming it) |
| Deleted internal paths (`HANDOFF.md`, `INTEGRATION.md`, `CROSS_REPO_PINS.md`, `LIVE_CLI.md`, `implementation-plan.json`, `messaging-plan.json`, `ACCEPT.md`, `e010-artifact-map.md`) | **absent** from `git rev-list --objects` |
| Secret filenames (`.env`, `modal.toml`, `*.pem`, `credentials.json`) | **absent** |

**CLEAN.** The replacement history meets the bar. GitHub `origin/main`
is not yet replaced; see the runbook. After force-push, repeat the
scan against a fresh `git clone --single-branch --branch main` of
GitHub — local clones of this workspace still contain dirty objects
via other refs.

The orphan SHA at the first isolated scan was
`8a051fdc7cfad1e684f72111d6d9da7b5e5d816e`. Recreating the orphan
after this CLEAN block lands will produce a **new** SHA with the
same properties (still 1 commit, same scanners). Compare trees, not
the old orphan SHA, before force-push.

## 4. Mac operator runbook — replace `origin/main`

Do this on a machine that can authenticate as the repo owner (Advitiya).
Keep the GitHub repo **private** throughout.

### 4.1 Preconditions

1. Read the risk section above.
2. Confirm `gh repo view advitiyavashist/atman --json visibility` is
   `PRIVATE`.
3. Confirm no in-flight merge to `main` that is not already in the orphan
   tree. If `origin/main` has moved, rebuild the orphan from the new tip
   (repeat the `commit-tree` step) before pushing.
4. Tell every clone / worktree owner to stop pushing to `main`.
5. Optional but recommended: snapshot the old graph.

   ```sh
   git fetch origin
   git branch backup/main-pre-t522 origin/main
   git push origin backup/main-pre-t522
   ```

   Leave `backup/main-pre-t522` **private-only**. Delete it before any
   public flip (it is the dirty history).

### 4.2 Fetch the orphan and compare trees

```sh
git fetch origin cursor/t522-orphan-main-9059
# Trees must match the reviewed scrubbed tip (this PR / branch):
git diff --stat origin/cursor/t522-history-rewrite-9059 origin/cursor/t522-orphan-main-9059
# expect empty

git rev-list --count origin/cursor/t522-orphan-main-9059
# expect 1

git merge-base --is-ancestor origin/cursor/t522-orphan-main-9059 origin/main
# expect exit 1 (orphan is not in old main — that is intended)
```

### 4.3 Force-push replacement `main`

```sh
git fetch origin main
git push --force-with-lease=main:$(git rev-parse origin/main) \
  origin origin/cursor/t522-orphan-main-9059:main
```

If lease fails, **stop**. `main` moved. Rebuild the orphan; do not
`--force` blindly.

### 4.4 Reset local checkouts

```sh
git fetch origin
git checkout main
git reset --hard origin/main
```

Other worktrees: same `fetch` + `reset --hard`, or delete and re-clone.

### 4.5 Remove dirty refs before any public flip

Force-pushing `main` does not delete the other ~155 branches. They still
advertise the old objects.

```sh
# Review, then delete every remote branch except main (and except a
# temporary backup you still need):
gh api repos/advitiyavashist/atman/branches --paginate --jq '.[].name' \
  | grep -v -x main \
  | grep -v -x backup/main-pre-t522 \
  | while read -r b; do git push origin --delete "$b"; done
```

Open PRs that point at old SHAs should be closed or reopened from the
new `main`. GitHub may still retain PR commits; that is why credential
rotation is not optional.

Tags: none existed at scan time. If any are created later from old SHAs,
delete them before flipping public.

### 4.6 Re-scan the rewritten GitHub `main`

```sh
rm -rf /tmp/atman-clean-verify
git clone --single-branch --branch main git@github.com:advitiyavashist/atman.git /tmp/atman-clean-verify
cd /tmp/atman-clean-verify
git rev-list --count HEAD   # must be 1
gitleaks detect --source . --no-banner
trufflehog git file://. --no-update
git grep -n '/Users/[A-Za-z0-9._-]+' $(git rev-list --all) || true
# allow only <operator> / someone test placeholders
test ! -e docs/HANDOFF.md
test ! -e docs/INTEGRATION.md
test ! -e docs/CROSS_REPO_PINS.md
```

### 4.7 Visibility (separate, owner-only)

```sh
# DO NOT run this as part of T-522.
# gh repo edit advitiyavashist/atman --visibility public
```

HOLD until Advitiya / CTO explicitly flip visibility after CLEAN verify
and leftover-ref deletion.

## 5. Why this Cloud Agent did not force-push `main`

The replacement orphan is pushed as
`cursor/t522-orphan-main-9059`. Rewriting `origin/main` is an owner
operation with irreversible collaboration cost. This agent does not
change GitHub visibility and does not force-push `main`.

## CLEAN re-scan (orphan isolate)

See section 3. Repeat after the Mac operator force-pushes `main` and
deletes leftover branches. A CLEAN orphan branch is not a CLEAN GitHub
object store while other refs still point at the old graph.
