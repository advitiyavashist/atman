# Public-release checklist

This repository is intended to be published as open source. Complete the
steps below **before** changing GitHub visibility. Visibility stays private
until Advitiya / CTO flip it in a separate step.

## 1. Working tree

Tip-only scrub landed in PR #16 (`0bce0de`, T-362 / T-366). T-522 redacts
a few internals that landed after that PR. Re-scan before a flip:

- Secrets (API keys, tokens, PEM/private keys, `.env`, credential JSON,
  database URLs with passwords).
- Confirm `.env` and other secret filenames stay gitignored.
- Use `.env.example` only for empty or placeholder names.
- No operator `/Users/<handle>/` paths except documented test placeholders.

## 2. Git history

A tree scrub does **not** remove secrets from old commits. Full-history
scan + replacement snapshot: [HISTORY_REWRITE.md](HISTORY_REWRITE.md).

Before a public flip:

1. Rotate every credential that ever appeared in this repository (or in a
   fork, worktree, or CI log derived from it).
2. Replace `origin/main` with the orphan snapshot
   `cursor/t522-orphan-main-9059` using the force-push runbook in
   `docs/HISTORY_REWRITE.md` (Advitiya / Mac operator). `--force-with-lease`
   only; do not rewrite `main` from a Cloud Agent unless the owner orders it.
3. Delete leftover remote branches (they still reach the old objects).
4. Scan the rewritten history again in a fresh single-branch clone.
5. Then — and only then — Advitiya / CTO may flip visibility.

Do not treat a prep PR merge as a history rewrite. The orphan branch is
the replacement; merging this documentation onto old `main` does not
remove dirty commits.

## 3. Visibility

Leftover dirty remote branches were deleted on 2026-09-08 (receipt:
[VISIBILITY_FLIP.md](VISIBILITY_FLIP.md)). `origin/main` is the CLEAN
orphan `abfcdae` (1 commit). The Cloud Agent **could not** flip GitHub
visibility (GitHub App token `403`). Advitiya still has to run:

```sh
gh repo edit advitiyavashist/atman --visibility public --accept-visibility-change-consequences
```

A green tree-scrub PR is not a visibility change.
