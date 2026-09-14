# T-824: explicit target repository for cross-repo spawn

The shared board may live under Steer while the deliverable is Atman.
`dirname(board)` is not git identity. `spawn --worktree` under an Atman
path is not enough: without `--repo`, `git worktree add` still used the
board/current repository, so Atman-looking trees were Steer object
databases, and PRs landed in the wrong repo. Selecting an existing Atman
checkout was also insufficient: that tree's Cursor hooks often pin
canonical `cursor`, so the child rebound the CoS identity.

## Contract

```sh
# Cross-repo (Steer board, Atman deliverable)
tickets spawn <seat> --repo /path/to/atman --base origin/main --harness cursor --persist

# Origin slug is fine when a sibling checkout matches
tickets spawn <seat> --repo advitiyavashist/atman --base origin/main

# Same-repo (board lives in the deliverable checkout): unchanged
tickets spawn <seat>
```

- `--repo` is a local checkout path or an `owner/repo` origin.
- Default worktree is `<repo>/.worktrees/<seat>` on `--base` (default
  `origin/main` when that ref exists, else local `main`/`master`).
- Spawn copies project settings from the **target** repo, then installs
  identity-pinned hooks for the unique seat in that new worktree.
- Ambiguous `--worktree` under a different origin than the board, with
  no `--repo`, fails closed.
- Reusing a shared checkout whose hooks already pin another agent fails
  closed. Dedicated `<repo>/.worktrees/<seat>` trees are overwritten.

## Detect a misbound worker

```sh
git -C "$WT" remote get-url origin
git -C "$WT" rev-parse --show-toplevel
git -C "$WT" rev-parse --git-common-dir
```

Misbound if the path looks like Atman (`.../atman/.worktrees/<seat>`) but
`origin` is `advitiyavashist/steer` or `--git-common-dir` is under the
Steer object database.

On the board:

```sh
tickets who
# worktree path must be the Atman tree; expected_origin / spawn_repo on
# workforce.json must be advitiyavashist/atman
```

## Repair

1. `tickets spawn <seat> --stop`
2. Remove the wrong linked worktree from the **Steer** repo (the object
   database that created it), not from Atman:
   `git -C /path/to/steer worktree remove --force "$WT"`
3. Confirm `git -C /path/to/atman worktree list` does not show that path.
4. Respawn with an explicit target:
   `tickets spawn <seat> --repo /path/to/atman --base origin/main --harness <h> --persist`
5. `tickets here` from the new tree. `tickets who` must show the Atman
   path. `git -C "$WT" remote get-url origin` must be Atman.
6. Review and submit from that tree so the pin is `advitiyavashist/atman`.

Do not `tickets init` a second board. Do not copy another seat's
`.cursor` / `.claude` hooks into the new tree.
