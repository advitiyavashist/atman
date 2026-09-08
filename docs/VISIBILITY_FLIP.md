# T-522 visibility flip receipt

Date: 2026-09-08. Operator: Cloud Agent (Advitiya GO via CTO).

This is the post-rewrite leftover-ref deletion + re-scan + visibility
attempt. It does **not** force-push `main`. Related: `docs/HISTORY_REWRITE.md`,
`docs/PUBLIC_PREP.md`, closed PR #37.

## 1. `origin/main` (unchanged)

| Check | Result |
|---|---|
| Tip | `abfcdae82e97022ceca07134c367ce675d3fe11a` |
| History | **1 commit** (orphan). Parents: none. |
| Message | `Initial public-safe snapshot of atman (T-522).` |
| Tree | `c13430d6f5f26f0f6ff8f53f3d91f22a9f414e02` |
| Force-push this step? | **No.** Tip was already the CLEAN orphan. |

## 2. Remote heads

Before: **165** heads. After: **3**.

Deleted **162** dirty remote branches (0 failures). Kept:

| Ref | SHA | Why |
|---|---|---|
| `main` | `abfcdae` | public-safe orphan |
| `cursor/t522-orphan-main-9059` | `abfcdae` | T-522 evidence (same tip) |
| `cursor/t522-history-rewrite-9059` | `00983626ef6f1db38517ae4b041b468914d98759` | T-522 evidence (3 commits, **same tree** as `main`) |

`cursor/t522-history-rewrite-9059` does **not** reach pre-rewrite `d11236f`
history. Isolated `git rev-list --count` on that ref is **3**.

## 3. Tags

`git ls-remote --tags origin` was empty before and after. Nothing deleted.

## 4. Re-scan of GitHub `main` tip

Method: `git clone --depth=1` of `origin/main` only (not this workspace's
shared object store, which still has dirty local objects).

| Tool | Scope | Result |
|---|---|---|
| gitleaks 8.30.1 | orphan history (1 commit) | **0 leaks** |
| gitleaks 8.30.1 | working tree (`gitleaks dir`) | **0 leaks** |
| trufflehog 2.2.1 (pip) | tree entropy/regex | noisy hex/SHA false positives only (git SHAs, `a1b2c3d4…` fixtures, `0123456789abcdefghijklmnopqrstuvwxyz`). No live tokens. |
| `git grep '/Users/…'` | tip tree | placeholders + this rewrite docs only (`/Users/someone`, `/Users/<operator>`). **No real operator home handle.** |
| Secret filenames | tip tree | no committed `.env`, `*.pem`, `credentials.json`, `modal.toml` |

**CLEAN** on the advertised `main` tip.

## 5. Visibility

| When | `gh repo view --json visibility` |
|---|---|
| Before leftover-ref delete | `PRIVATE` |
| After leftover-ref delete + CLEAN scan | `PRIVATE` |
| After `gh repo edit … --visibility public` | **still `PRIVATE`** |

Blocker: the Cloud Agent GitHub App installation token (`ghs_…`, account
`cursor`) returns **HTTP 403** `Resource not accessible by integration`
on `PATCH /repos/advitiyavashist/atman` (visibility). GitHub Apps cannot
change repository visibility; that needs the owner account (Advitiya)
in the UI or a classic PAT with admin / `delete_repo`.

Command that failed:

```sh
gh repo edit advitiyavashist/atman --visibility public --accept-visibility-change-consequences
```

Owner-only completion (do **not** force-push `main`):

```sh
# as Advitiya
gh repo view advitiyavashist/atman --json visibility
# expect PRIVATE

git ls-remote --heads origin
# expect only:
#   main
#   cursor/t522-orphan-main-9059
#   cursor/t522-history-rewrite-9059
# (plus this receipt branch if still open)

gh repo edit advitiyavashist/atman --visibility public --accept-visibility-change-consequences
gh repo view advitiyavashist/atman --json visibility
# expect PUBLIC
```

## 6. Residual risk (not rotated here)

- GitHub still retains **closed/merged PR commits** from the pre-rewrite
  graph (PRs #10–#38 and others). Those SHAs can remain fetchable via
  the PR UI after branch deletion.
- **PR #39** (`grok-worker/t520-landing-recut` @ `622b074`) was **OPEN**
  after its dirty head was deleted. This agent could not close it
  (`403` / MCP `404`). Close it as the owner; GitHub may still keep the
  PR commits.
- **0 forks** at scan time. If anyone forked while private, old objects
  can survive there.
- Do not treat scanner CLEAN as credential rotation. Rotate anything
  that ever lived in this repo, a worktree, CI, or a PR.

`advitiyavashist/steer` and `ati` were not touched.
