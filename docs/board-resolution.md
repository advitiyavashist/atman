# Which board am I about to write to?

Two different questions, answered by two different resolvers. Confusing them
is T-263.

- **Ambient** — "where will `tickets <anything>` from this cwd go?"
  `board_dir()` → `_board_dir_uncached()` → `_repo_root()`.
- **Init target** — "where would `tickets init` install a board?"
  `_init_cwd_worktree_root()`.

They are computed independently on purpose. `init` compares them and refuses
when they disagree; if both came from `_repo_root()` the comparison would be
`x == x` and the refusal would be unreachable. That was the first fix's defect
(T-282), and it was unreachable in the common linked-worktree configuration.

## Ambient precedence (`_board_dir_uncached`)

First match wins. **The order is the bug** T-263 was filed against: the top
two entries beat anything `init` could create in the current directory.

| # | Rule | Notes |
|---|------|-------|
| 1 | `$TICKETS_DIR` | Honoured unconditionally, before anything on disk. Every agent session exports it. |
| 2 | `<main worktree>/.tickets` | From `_repo_root()`, which resolves `--git-common-dir`. **Every linked worktree of a repo shares the main worktree's board.** Taken whenever the board exists, or whenever cwd is not itself the main worktree root. |
| 3 | Nearest `.tickets` walking up from cwd | The ancestor case: a fresh `git init` nested under a project that already has a board finds the ancestor's. |
| 4 | `<nearest .git dir>/.tickets` | |
| 5 | A single child board one level down | Only when `discover_children` is on. |
| 6 | `<cwd>/.tickets` | Last resort. |

Rule 2 is deliberate and load-bearing: the whole fleet works in linked
worktrees of one repo and must see one board. Do not "fix" it by making cwd
win — that forks the board per worktree.

## Init target (`_init_cwd_worktree_root`)

The worktree cwd is **literally** in: the first ancestor holding a `.git`
entry, found by walking the filesystem. `git rev-parse --show-toplevel` is
asked only as a cross-check and the filesystem wins on disagreement (T-243:
`GIT_DIR`/`GIT_COMMON_DIR` in the environment can redirect git, and cannot
redirect a directory walk). Never `--git-common-dir`, so in a linked worktree
this returns the worktree, not the main checkout.

`--board DIR` overrides it entirely.

## What `init` does

1. Prints `board: <target>` and how it resolved it — **before the first
   write**, so the question in this file's title is answerable in advance.
2. If target ≠ ambient and no `--board` was given: **refuses and writes
   nothing**, naming the cause (TICKETS_DIR / linked worktree / ancestor
   board) and the three ways out.
3. Writes `.tickets/MASTER.md`, `.cursor/rules/tickets.mdc`, and appends to
   `AGENTS.md` and `.gitignore` — all rooted at the **target**, never at
   `dirname(ambient)`.
4. Re-runs the real resolver and **verifies** the bind. Without `--board` a
   failed bind exits non-zero. With `--board` it prints `NOT BOUND` and the
   exact `export TICKETS_DIR=…` line, because an explicit target the caller
   asked for is allowed not to bind — but never silently.

## Why refusing is the right answer in a linked worktree

`init` cannot honestly bind there without breaking rule 2 for everyone else.
So it fails loudly. Acceptance 1 of T-263 is "creates AND binds, or fails
loudly" — not "always succeeds".

    export TICKETS_DIR=/path/to/worktree/.tickets   # bind this directory
    tickets init --board /path/to/main/.tickets     # install into the real board
    cd /path/to/main && tickets init                # or just go there

## Known divergence, not fixed here

`_repo_root()` in `tickets.py` carries T-243's hardening (scrubbed `GIT_*`
env, filesystem cross-check); the copy in `src/ticket_board/cli.py` is still
the older four-line version that calls `git rev-parse --git-common-dir` with
the ambient environment. That is a real gap in the *ambient* resolver and
belongs to T-243's area, not this ticket. `_init_cwd_worktree_root()` is
self-contained precisely so it behaves identically in both copies regardless.
