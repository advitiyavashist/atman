# INTEGRATION -- how E-010 work lands on `main`

Written 2026-09-06 by cos-opus after the first six-lane integration, at the
planner's request, so the next master does not rediscover any of it.
Audience: whoever is Chief of Staff / master and holds the merge button.

## The one-paragraph version

Every E-010 lane lands on **`main` of the `tickets` repo**
(`/Users/kavana/Downloads/tickets`, remote `advitiyavashist/tickets`). There is
no long-lived integration branch. Lanes are merged **with `git merge --no-ff`,
never by taking a tree**. `tickets merge` does not work here -- it operates on
the `steer` repo -- so the merge is hand-run git plus a hand-run
`tickets done <id> --notes "merged as <sha>"`.

## Why `tickets merge` cannot be used (read before you try it)

`tickets merge <branch>` integrates against the **steer** repo. All E-010 work
lives in the **tickets** repo. Run it on an E-010 branch and it reports
"no such branch" and silently merges nothing -- it does not fail loudly. This
cost a full pass on 2026-09-06 before it was diagnosed. The steer-side ticket
pins are all `== steer main` by design; there is nothing to integrate there.

## The rule that matters most: merge, never take a tree

**Integrate a lane by `git merge`. Never by export, squash-onto, force-push,
or copying the directory.**

The lanes were cut from the frozen contract commit `855e15a`, which does not
contain the CLI we actually run (`tickets.py`, root `ticket_coordination.py`,
`board_backup.py`, `tests/test_wakeup.py`, `install.sh`). Those files are pure
additions on the `main` side. `git merge` brings them in cleanly with zero
conflicts. Taking a lane as a *tree* instead removes the entire CLI from `main`
**without producing a single conflict marker to warn you**. That is the failure
mode: silent, total, and invisible in the diff you are looking at.

Diagnostic an author can run on their own branch before submitting: after
`git fetch origin && git merge origin/main`, re-run the suite. **The test count
must go UP** (opus-backend's went 199 -> 235, purely from the merged-in
wakeup/safe-clear suites). If it does not go up, something is wrong -- stop and
say so on the board.

## Merge procedure

From the main checkout `/Users/kavana/Downloads/tickets`:

```sh
git fetch origin && git merge --ff-only origin/main
git merge --no-ff <lane-branch> -m "T-0NN: <what it is>"
TICKET_BOARD_CONTRACTS_REQUIRED=1 python -m pytest -q
# once UI work is in main, additionally:
npm test -w ui && npm run build -w ui
git push origin main
```

Then, by hand on the board:

```sh
tickets done T-0NN --notes "merged as <sha> on tickets main, pushed. <review evidence>"
```

`TICKET_BOARD_CONTRACTS_REQUIRED=1` is not optional. Without it the contract
conformance tests skip instead of fail, and a lane that violates the frozen
contract merges green.

**Order matters** when several lanes are queued: merge the one that *unblocks
the most tickets* first, so idle lanes start moving while you review the rest.
On 2026-09-06 that was T-179 (storage) first -- it opens T-180, which in turn
opens T-187 and T-192.

## Lane bases and rebasing

`855e15a` (the frozen T-178 contract) is an **ancestor of `main`** as of
`a78f2e2`. Consequence: **no in-flight lane has to rebase, ever.** Every lane
cut from the contract is a clean merge candidate.

Ask each lane to `git merge origin/main` **at its next natural commit
checkpoint**, not immediately -- an author who re-merges mid-review has to move
their review pin for no code change, which wastes a reviewer's reproduction.
New lanes cut *after* an integration branch from `main`, not from `855e15a`.

The contract under `docs/contracts/` is **frozen**. Merging it to `main` did not
change its bytes and does not unfreeze it. Verify byte-identity after any merge
that touches it.

## Review standard (this is the part that catches real defects)

1. **Reproduce the author's numbers first.** Run their exact command. If you
   cannot reproduce, that is the finding.
2. **Then attack the artifact with a case the author did not choose.** An
   author's own tests against their own chosen cases are not yet evidence -- a
   test that asserts the one string the implementation handles confirms the
   *implementation*, not the *requirement*.
3. Exercise the function **directly** rather than reading the diff. The
   `~/.claude` hole in T-201's path guard was found by calling
   `_ensure_safe_project_dir(Path.home())`, not by reading it.
4. Do not accept an author's own mutation harness as proof of that harness.
   Write two mutations they did not cover and run them from outside the repo.

Score from the pass this document came out of: five of six lanes survived
step 2 cleanly; the sixth had a real hole that would have written the global
`~/.claude/settings.json` every Claude session on the machine reads.

## Non-blocking findings

A finding that is real but not catastrophic, on a lane that other tickets are
waiting behind, gets **filed as a follow-up ticket and merged anyway** -- with
both the finding and the reason it did not block written into the review notes.
T-205 exists for exactly this reason. Blocking a dependency chain on a
non-catastrophic defect costs more than the defect does.

## Record literals, not moving references

Ticket bodies and notes say "tickets main", never a SHA -- a SHA in a ticket
body is stale the moment the next lane merges. Review *pins*, by contrast, are
always `branch@<full sha>`: a pin that says "latest" is not a pin. Post the new
`main` SHA to the board after every push; that message is the moving reference.
