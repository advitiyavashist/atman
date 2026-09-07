# Cross-repo review pins (T-272)

## What was wrong

`tickets review` recorded `branch@sha` and `repo` from **the directory the agent
ran the command in**, not from the repo the deliverable is in. Under the hood
every probe went through a `git()` helper that inherited `os.getcwd()`, so the
branch, the sha and the repo identity were one triple taken from one wrong tree.

On this board that is not an edge case, it is the norm: every E-010 agent drives
the CLI from its `steer` worktree while the deliverable lives in
`advitiyavashist/tickets`.

T-215's guard — "only close a ticket from the repo its pin names" — was working
exactly as designed. It was aimed at the wrong repo, which is worse than no
guard, because it reads as protection. It failed in both directions on the same
ticket:

- the **correct** merge, run in `advitiyavashist/tickets`, looked cross-repo
  against the recorded `steer` identity, so the guard **refused to close it**;
- an **unrelated** `steer` merge that happened to contain the recorded sha was
  same-repo, so the guard **did not refuse**, and ancestry could close the
  ticket on a commit containing none of the work.

The review queue showed how bad the second half was in practice
(snapshot 2026-09-07T03:42Z, 12 tickets in review):

| ticket | recorded commit | recorded repo |
|---|---|---|
| T-181 | `opus-infra@c1d4706` | *(none — pre-T-215)* |
| T-217 | `sonnet-deploy@c1d4706` | *(none — pre-T-215)* |
| T-263 | `opus-verify@c1d4706` | `advitiyavashist/steer.git` |
| T-264 | `opus-backend@c1d4706` | `advitiyavashist/steer.git` |
| T-265 | `sonnet-tickets@c1d4706` | `advitiyavashist/steer.git` |
| T-266 | `opus-verify@c1d4706` | `advitiyavashist/steer.git` |
| T-268 | `sonnet-console@c1d4706` | `advitiyavashist/steer.git` |
| T-271 | `opus-authz@c1d4706` | `advitiyavashist/steer.git` |
| T-273 | `opus-verify@c1d4706` | `advitiyavashist/steer.git` |
| T-275 | `sonnet-tickets@c1d4706` | `advitiyavashist/steer.git` |
| T-244 | `cursor/cos-s05-wave-d83a@a8b9c3d` | `advitiyavashist/steer.git` |
| T-184 | `opus-console/t184-live-wiring@08f452b` | `advitiyavashist/tickets.git` |

**Ten of the twelve carry the identical sha `c1d4706`** — the steer branch head
that every agent worktree is parked on. Ten different tickets, ten different
deliverables, all pinned to one commit that contains none of their work; eight
of those name `steer` as the repo and two record no repo at all. Every one of
these deliverables actually lives in `advitiyavashist/tickets`.

Only T-184 was correct, and only because that agent happened to run `review`
from the tickets checkout rather than from its steer worktree.

Agents had been hand-writing the real sha into free-text `--notes` for days as a
workaround. That is the symptom this fixes.

## The fix

`--artifact <dir>` names the checkout the deliverable is in. It is accepted by
`review`, `done`, `merge` and `sync`.

It takes a **directory, not an assertion**. Branch, sha and repo are still
derived by running git inside that tree, so a pin no real tree can produce
cannot be recorded. This is deliberately *not* a `--repo`/`--commit` pair taken
on trust: a hand-typed sha is exactly how fiction gets into the close record,
and the close record is the thing being repaired.

```sh
# submit work that lives in another repo
tickets review T-264 --artifact ~/Downloads/tickets/.worktrees/opus-backend-t264 \
  --notes "paths, tests, decisions"

# the master integrates in the repo the artifact is in
tickets merge opus-backend/t264-none-branch-principal --artifact ~/Downloads/tickets

# closing by hand, from the board's repo, still works
tickets done T-264 --artifact ~/Downloads/tickets --notes "..."
```

`sync` takes it too, and must be given it: `review` now checks "is your branch
behind trunk" **in the artifact tree**, so the sync that clears that check has to
happen in the same tree. Without it, "sync before review" silently syncs a repo
the review never inspects.

### What gets recorded

| field | meaning |
|---|---|
| `repo` | the **artifact** repo. Still the field `tickets merge` compares, so no existing record needs a field rename. |
| `commit`, `branch` | derived from the same tree as `repo` — the triple is always internally consistent. |
| `repo_source` | `artifact` if the agent aimed it, `cwd` if the tool inherited it. Lets a reader tell a verified pin from a defaulted one. |
| `cwd_repo`, `cwd_commit` | where the command was actually run, kept as provenance, only when it differs. |

T-215's guard code is unchanged. Only what feeds it moved.

## Backfill: the tickets already queued with a wrong pin

Use `tickets repin`. It re-derives the pin from a real tree and leaves an
explicit `REPIN:` note recording what it replaced — it does **not** reset
`review_at` and does **not** re-notify the master, which re-running
`tickets review` on a queue this deep would do once per ticket.

```sh
# 1. put the artifact tree on the commit that was actually reviewed
#    (the real sha is in the ticket's own notes -- agents hand-wrote it there)
git -C ~/Downloads/tickets/.worktrees/<agent>-<ticket> checkout <branch>

# 2. correct the pin
tickets repin T-264 --artifact ~/Downloads/tickets/.worktrees/opus-backend-t264 \
  --notes "pin named steer; artifact is in the tickets repo"

# 3. verify -- repin prints was/now, and refuses a dirty tree
```

`repin` refuses a ticket that is not IN REVIEW. A closed ticket's record is
history; correcting a pin still awaiting merge is not the same act as rewriting
one that already closed.

### Tickets whose owner cannot run it

`gpt-codex` is out until 2026-09-12 and `gpt-cursor` until 2026-10-05. `repin`
derives its evidence from the tree, not from who runs it, so **the merger can
repin on their behalf** — they only need the right worktree at the right sha.
`git worktree list` in `~/Downloads/tickets` maps every branch to its checkout.
Say so in the `--notes` when you do.

### Pins with no `repo` field at all

T-181, T-217 and T-221 predate T-215. The merge guard already refuses to
auto-close them and says so by name. `repin` fixes them the same way; there is
no separate path.

## A merge guard that used to fail silently

`tickets merge` fast-forwards **the branch that is checked out**, while the
close loop tests ancestry against `trunk`. If the repo is parked on anything
else those are two different commits, so every ticket was skipped by a bare
`continue` with no reason printed — indistinguishable from the repo guard
rejecting them.

That was unreachable while `merge` could only ever run in the board's own repo,
which the master keeps on `main`. `--artifact` makes it reachable, so `merge`
now refuses out loud, naming the branch and the fix. This is the same "do not
leave the merger guessing" failure that produced the nine bad closes in T-253.

## Getting this into the tool agents actually run

Merging to `main` changes nothing about the live board. `tickets` resolves to a
shim that `exec`s a **sha-pinned release snapshot**:

```
~/.local/bin/tickets -> ~/.claude/tools/tickets.py   (196-byte shim)
  -> ~/.claude/tools/tickets-releases/<sha>/tickets.py
```

The release step, per T-223:

```sh
cd ~/Downloads/tickets
./install.sh --live-release --ref <merged sha>
```

Two things to know before cutting a release with this change in it:

1. **The live release is not on `main`.** As of 2026-09-07 the shim points at
   `1c7a6227`, which is T-223's own in-flight work and is *not* an ancestor of
   `main`. Releasing a `main` sha will therefore also ship — or drop — whatever
   else differs. Diff the two before cutting.
2. **Do not hand-copy.** Hand-copying is the drift T-223 exists to replace, and
   a hand-patch that drops the executable bit silences every agent at once.
