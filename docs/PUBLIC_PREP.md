# Public-release checklist

This repository is intended to be published as open source. Complete the
steps below **before** changing GitHub visibility. Visibility stays private
until the repository owner flips it.

## 1. Working tree

- Re-scan the current tree for secrets (API keys, tokens, PEM/private keys,
  `.env` files, credential JSON, database URLs with passwords).
- Confirm `.env` and other secret filenames stay gitignored.
- Use `.env.example` only for empty or placeholder names.

## 2. Git history

A tree scrub does **not** remove secrets from old commits. History may still
contain credentials, machine paths, or internal notes that used to live in
this repo.

Before a public flip:

1. Rotate every credential that ever appeared in this repository (or in a
   fork, worktree, or CI log derived from it).
2. If history still holds secrets, rewrite it with
   [`git filter-repo`](https://github.com/newren/git-filter-repo) or
   [BFG Repo-Cleaner](https://rtyley.github.io/bfg-repo-cleaner/), then
   force-push only after the owner approves a history rewrite.
3. Scan the rewritten history again.

Do not rewrite published history from a prep PR. Treat history cleanup as a
separate, owner-approved step.

## 3. Visibility

Leave the GitHub repository **private** until the owner (Advitiya) toggles
it public. A green tree-scrub PR is not a visibility change.
