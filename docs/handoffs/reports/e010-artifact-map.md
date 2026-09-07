# E-010 artifact map — where the review queue's work actually lives

**Ticket:** T-281 · **Authors:** opus-backend-2 (first pass), composer (refresh)
**First pass:** 2026-09-07T03:58Z (tickets main `07cd74c`)
**opus-backend-2 refresh:** 2026-09-07T08:2xZ — tickets `origin/main` = `5200f9d`, steer = `564557b`
**composer refresh:** 2026-09-07T18:24Z — atman `origin/main` = `ed5eb624ecd9ac3940a06ba2c1fd857328ec54cb`, steer `origin/main` = `822d0490660af659ab08ae293e991ba3b3cb2bfc`
**composer delta (T-388 recycle):** 2026-09-07T18:40Z — **atman `origin/main` = `a401ac3429f5bf4fe87104caa5d45d3e4b0d5d5a`**, steer `origin/main` = `2fd11b9299779082fe83395aa0c6a5832c63c119`

> **Repo rename note:** `advitiyavashist/tickets` is now `advitiyavashist/atman` (same
> codebase). This map uses "atman" for the product repo and "steer" for the coordination
> repo. The `/Users/kavana/Downloads/tickets` checkout shares the atman object store.

This document resolves every review-queue pin to its real repo, branch and sha so
the merge wave can run off evidence instead of off the recorded `commit` field.
It changes nothing. No merge, no fast-forward, no force-push, and no `tickets
review` was re-run to "fix" a pin — re-pinning from the wrong cwd is the defect
(T-272), not the remedy. Corrections are **named here and left for the master.**

> **Read this first if you read nothing else.** opus-backend-2's first pass told
> you to merge `sonnet-qa/t221-ui-mentions`. **That recommendation is retracted**
> — see [T-221 is superseded by T-276](#t-221-is-superseded-by-t-276-prior-recommendation-retracted).
> And `merge-base --is-ancestor` **alone is not sufficient**: it returns a false
> NO on work that landed by rebase. Use all three checks in [Method](#method--three-questions-per-row-not-one).

## Composer delta — +3 rows since 18:24Z (2026-09-07T18:40Z, post T-388 recycle)

| change | detail |
|---|---|
| Queue size | **46 → 49** rows (+T-394, T-395, T-397) |
| atman main | `ed5eb62` → `a401ac3` (+T-322 PR#22, T-372 PR#34, T-380 PR#35) |
| steer main | `822d049` → `2fd11b9` (+T-383/T-397 cite-badge PR#125, T-378 PR#126) |
| Now on main | **T-322** (`3cc821d`), **T-397** verify-only (`c07f5ac` on steer main) |
| New atman row | **T-394** `grok-worker/t394-silent-reopen@1cb130e` — pin correct, 1 origin ref |
| New ati row | **T-395** verification — real work in `advitiyavashist/ati`, steer pin is artefact |
| T-388 CLI | Release `21ca63c` live fleet-wide; T-377/T-392 bindings now measured post-recycle |

## Composer refresh — 46 live review-queue rows (2026-09-07T18:24Z)

Provenance: ambient `GIT_*` unset; atman commands from
`/Users/kavana/Downloads/atman/.worktrees/composer-t281` (`git rev-parse
--show-toplevel` = `/Users/kavana/Downloads/atman`); steer read-only from
`/Users/kavana/Downloads/steer`. Board JSON read from `.tickets/` (not edited).

### Summary since opus-backend-2's 9-row pass

| change | detail |
|---|---|
| Queue size | **9 → 46** rows (E-010 + E-011 + verification wave) |
| atman main | `5200f9d` → `ed5eb624ecd9` (+T-377 product, trajectories, scheduler, turns, BYOA, landing, …) |
| steer main | `564557b` → `822d0490660a` |
| Pin quality trend | **Most new reviews pin atman correctly.** Wrong-pin defect (T-272) largely worked around. |
| Critical wrong pin | **T-244 pins T-228's branch** (`opus-authz/t228-same-second-inbox@8727c09`) — real artifact is `sonnet-backend/t244-inbox-rotation-archive@f7b5d0c` |
| Durability gaps | **T-291, T-328, T-353** — claimed shas have **0 `refs/remotes/origin`** (local-only) |
| Already on main | **T-377** (product+test via PR#26/#36), **T-392** residual test landed same |
| External repos | **T-384, T-390** live in `advitiyavashist/ati` (not atman) |

### The map — 46 rows

| ticket | pin repo | recorded pin | real branch | real sha | on-main? | unlanded + | durable? | pin-correct? |
|---|---|---|---|---|---|---|---|---|
| T-187 | atman | `opus-backend-2/t187-messaging-api@4cfeaf5` | `opus-backend-2/t187-messaging-api` | `4cfeaf5` | NO | 1 | 3 ref(s) | YES |
| T-188 | atman | `opus-infra/t188-managed-runner@1d11f50` | `opus-infra/t188-managed-runner` | `1d11f50` | NO | 5 | 1 ref(s) | YES |
| T-189 | atman | `sonnet-console/t189-messages-ui@47a2f68` | `sonnet-console/t189-messages-ui` | `47a2f68` | NO | 1 | 1 ref(s) | YES |
| T-228 | atman | `opus-authz/t228-same-second-inbox@8727c09` | `opus-authz/t228-same-second-inbox` | `8727c09` | NO | 5 | 2 ref(s) | YES |
| T-244 | atman | `opus-authz/t228-same-second-inbox@8727c09` | `sonnet-backend/t244-inbox-rotation-archi` | `f7b5d0c` | NO | 3 | 3 ref(s) | NO — pins T-228 branch |
| T-266 | atman | `infra-2/t266-hooks-dedup-scope@bc12b35` | `infra-2/t266-hooks-dedup-scope` | `bc12b35` | NO | 2 | 1 ref(s) | YES |
| T-273 | atman | `opus-verify/t273-tmpdir-guard-v2@fd95689` | `opus-verify/t273-tmpdir-guard-v2` | `fd95689` | NO | 1 | 1 ref(s) | YES |
| T-280 | atman | `opus-console/t280-bound-port@c7c4b6a` | `opus-console/t280-bound-port` | `c7c4b6a` | NO | 1 | 1 ref(s) | YES |
| T-285 | steer | `sonnet-sdk@564557b` | `sonnet-sdk` | `564557b` | YES | n/a | 10 ref(s) | YES (steer work) |
| T-286 | atman | `sonnet-backend/t286-invitation-replay-fi@d6e9027` | `sonnet-backend/t286-invitation-replay-fi` | `d6e9027` | NO | 3 | 1 ref(s) | YES |
| T-288 | atman | `opus-backend/t288-contract-conformance@c0db243` | `opus-backend/t288-contract-conformance` | `c0db243` | NO | 1 | 1 ref(s) | YES |
| T-290 | steer | `cursor/cos-s05-wave-d83a@a8b9c3d` | `cursor/cos-s05-wave-d83a` | `a8b9c3d` | YES | n/a | 19 ref(s) | verification — steer pin is artefact |
| T-291 | atman | `infra-2/t291-release-drift-warning@d100fa1` | `infra-2/t291-release-drift-warning` | `d100fa1` | NO | n/a | 0 refs | repo OK, sha NOT DURABLE (0 origin refs) |
| T-293 | steer | `sonnet-qa-t221-ui-mentions@94d2c26` | `sonnet-qa-t221-ui-mentions` | `94d2c26` | NO | n/a | 0 refs | verification artefact |
| T-294 | steer | `opus-console@564557b` | `opus-console` | `564557b` | YES | n/a | 10 ref(s) | verification — steer pin is artefact |
| T-296 | steer | `sonnet-sdk@564557b` | `sonnet-sdk` | `564557b` | YES | n/a | 10 ref(s) | verification — steer pin is artefact |
| T-297 | atman | `opus-backend/t297-acceptance-write-path@8909356` | `opus-backend/t297-acceptance-write-path` | `8909356` | NO | 1 | 1 ref(s) | YES |
| T-299 | steer | `sonnet-backend@564557b` | `sonnet-backend` | `564557b` | YES | n/a | 10 ref(s) | verification — steer pin is artefact |
| T-300 | steer | `opus-verify@564557b` | `opus-verify` | `564557b` | YES | n/a | 10 ref(s) | verification — steer pin is artefact |
| T-301 | atman | `sonnet-console/t301-bounded-read@0763c6d` | `sonnet-console/t301-bounded-read` | `0763c6d` | NO | 1 | 1 ref(s) | YES |
| T-302 | steer | `opus-liveness@564557b` | `opus-liveness` | `564557b` | YES | n/a | 10 ref(s) | verification — steer pin is artefact |
| T-303 | steer | `sonnet-sdk@564557b` | `sonnet-sdk` | `564557b` | YES | n/a | 10 ref(s) | verification — steer pin is artefact |
| T-304 | steer | `opus-verify@564557b` | `opus-verify` | `564557b` | YES | n/a | 10 ref(s) | verification — steer pin is artefact |
| T-305 | steer | `sonnet-qa-t221-ui-mentions@94d2c26` | `sonnet-qa-t221-ui-mentions` | `94d2c26` | NO | n/a | 0 refs | verification — steer pin is artefact |
| T-306 | steer | `opus-authz@564557b` | `opus-authz` | `564557b` | YES | n/a | 10 ref(s) | verification — steer pin is artefact |
| T-308 | steer | `sonnet-tickets@564557b` | `sonnet-tickets` | `564557b` | YES | n/a | 10 ref(s) | verification — steer pin is artefact |
| T-310 | steer | `sonnet-console@564557b` | `sonnet-console` | `564557b` | YES | n/a | 10 ref(s) | verification — steer pin is artefact |
| T-321 | steer | `sonnet-qa-t221-ui-mentions@94d2c26` | `sonnet-qa-t221-ui-mentions` | `94d2c26` | NO | n/a | 0 refs | verification artefact |
| T-322 | atman | `opus-console/t322-quickstart@3cc821d` | `opus-console/t322-quickstart` | `3cc821d` | YES | 0 | 2 ref(s) | YES — landed PR#22 |
| T-324 | atman | `sonnet-sdk/t324-ancestry-pin-guard@8dc234c` | `sonnet-sdk/t324-ancestry-pin-guard` | `8dc234c` | NO | 1 | 1 ref(s) | YES |
| T-326 | atman | `opus-verify/t326-t187-acceptance@b7a7e98` | `opus-verify/t326-t187-acceptance` | `b7a7e98` | NO | 2 | 1 ref(s) | YES |
| T-327 | atman | `grok-worker/t327-join-inbox@0f0e54a` | `grok-worker/t327-join-inbox` | `0f0e54a` | NO | 3 | 1 ref(s) | YES |
| T-328 | atman | `infra-2/t328-migration-txn@98f18ed` | `infra-2/t328-migration-txn` | `98f18ed` | NO | n/a | 0 refs | repo OK, sha NOT DURABLE (0 origin refs) |
| T-329 | atman | `opus-authz/t329-cli-inbox-clamp-port@91b7a05` | `opus-authz/t329-cli-inbox-clamp-port` | `91b7a05` | NO | 6 | 1 ref(s) | YES |
| T-330 | steer | `sonnet-deploy@564557b` | `sonnet-deploy` | `564557b` | YES | n/a | 10 ref(s) | verification — steer pin is artefact |
| T-331 | steer | `opus-liveness@564557b` | `opus-liveness` | `564557b` | YES | n/a | 10 ref(s) | verification — steer pin is artefact |
| T-344 | steer | `composer@d7da471` | `composer` | `d7da471` | NO | n/a | 1 ref(s) | verification artefact |
| T-345 | steer | `composer@d7da471` | `composer` | `d7da471` | NO | n/a | 1 ref(s) | verification artefact |
| T-353 | atman | `composer/t353@b40a03e` | `composer/t353` | `b40a03e` | NO | n/a | 0 refs | repo OK, sha NOT DURABLE (0 origin refs) |
| T-354 | steer | `cursor-fable@564557b` | `cursor-fable` | `564557b` | YES | n/a | 10 ref(s) | verification — steer pin is artefact |
| T-367 | steer | `composer@d7da471` | `composer` | `d7da471` | NO | n/a | 1 ref(s) | verification artefact |
| T-376 | steer | `cursor/t340-30s-demo@84e08ef` | `cursor/t340-30s-demo` | `84e08ef` | NO | n/a | 0 refs | verification artefact |
| T-377 | atman | `composer/t377-env-isolate@a27765f` | `composer/t377-env-isolate` | `a27765f` | YES | 0 | 3 ref(s) | YES — work ON MAIN |
| T-384 | ati | `cursor-modal/t384-bc-smoke@8ca7cec` | `cursor-modal/t384-bc-smoke` | `8ca7cec` | NO | n/a | 0 refs | YES (ati repo) |
| T-390 | ati | `cursor-modal/t390-protocol@d3ec51e` | `cursor-modal/t390-protocol` | `d3ec51e` | NO | n/a | 0 refs | YES (ati repo) |
| T-392 | steer | `composer@d7da471` | `composer/t377-env-isolate` | `a27765f` | YES | 0 | 3 ref(s) | YES — work ON MAIN |
| T-394 | atman | `grok-worker/t394-silent-reopen@1cb130e` | `grok-worker/t394-silent-reopen` | `1cb130e` | NO | 1 | 1 ref(s) | YES |
| T-395 | steer | `composer@d7da471` | *(ati verification)* | — | n/a | n/a | n/a | verification — ati repo; steer pin is artefact |
| T-397 | steer | `composer@d7da471` | `cursor/t-383-cite-badge-quiet-0908` | `c07f5ac` | YES | 0 | 1 ref(s) | YES — verify-only, ON MAIN (PR#125) |

### Findings that change the merge plan (composer pass)

1. **T-244 wrong pin is live and dangerous.** The board record copies T-228's
   branch/sha. Real work is `sonnet-backend/t244-inbox-rotation-archive@f7b5d0c`
   (3 unlanded commits per opus-backend-2's prior analysis). Merging at the
   recorded pin would merge the wrong branch.

2. **Three atman deliverables are not durable.** T-291 (`d100fa1`), T-328
   (`98f18ed`), T-353 (`b40a03e`) have zero `refs/remotes/origin` containing
   their shas. Authors must push before merge desk acts.

3. **T-377 and T-392 are on main.** `a27765f` is an ancestor of `ed5eb62`
   (PR#36). Desk can close both; steer pins on T-392 are review artefacts.

4. **Verification rows (steer `564557b` pins)** — T-290, T-294, T-296, T-299,
   T-300, T-302–T-306, T-308, T-310, T-330, T-331, T-354: no deliverable
   commit; steer pin is on steer main (auto-close armed). Not errors.

5. **T-276 likely landed** — check `git cherry` before merging; main now
   contains `test_t221_mentions.py` and large `tickets.py` rewrite from the
   atman main merge.

---

## Provenance of every command in this document

Per T-243, cwd alone does not establish which repo a git command answers for.

- Ambient git environment checked before the first git command:
  `env | grep '^GIT_'` returned only `GIT_EDITOR=true`. **No `GIT_DIR`,
  `GIT_COMMON_DIR` or `GIT_WORK_TREE` was set**, so cwd-based discovery is
  trustworthy in this session.
- Every "tickets repo" command ran in a worktree whose
  `git rev-parse --show-toplevel` resolves inside the
  `/Users/kavana/Downloads/tickets` object store (worktree
  `…/scratchpad/t281`, branch `opus-backend-2/t281-artifact-map`).
  **The tickets main checkout was never entered and never modified.**
- Every "steer repo" command ran with
  `git rev-parse --show-toplevel` = `/Users/kavana/Downloads/steer`, read-only
  (`fetch`, `rev-parse`, `merge-base`, `for-each-ref`).
- Board records were read from `/Users/kavana/Downloads/steer/.tickets/T-*.json`
  (read-only; nothing under `.tickets/` was edited).

Trunks resolved after a fresh `git fetch origin --prune` in each repo:

| repo | trunk at this refresh |
|---|---|
| `advitiyavashist/atman` (was tickets) | `origin/main` = **`ed5eb624ecd9`** (composer refresh) |
| `advitiyavashist/steer` | `origin/main` = **`822d0490660a`** (composer refresh) |

## Method — three questions per row, not one

The first pass used two checks. **Two is not enough**; T-237 is the
counterexample and it is in this queue.

- **on-main? (ancestry)** — `git merge-base --is-ancestor <sha> origin/main`.
  Checking the commit out and seeing it resolve proves nothing: orphaned commits
  check out perfectly clean. "exists" and "is on main" are different questions.
- **landed? (content)** — `git cherry -v origin/main origin/<branch>`. A `-`
  marks a commit whose **patch is already upstream under a different sha**; `+`
  marks genuinely unlanded work. **This is the check the first pass lacked.**
  Ancestry answers "is this exact commit on main"; cherry answers "is this
  *work* on main". Rebase and cherry-pick make those two disagree, and the
  merge wave has been cherry-picking.
- **durable?** — `git for-each-ref refs/remotes/origin --contains <sha>`. A
  commit reachable only from the shared local clone is one disk failure from
  gone. Neither check above can see this.

**Why the second check matters, concretely.** T-237's pinned `334a12d` is
**not** an ancestor of `5200f9d`. On ancestry alone T-237 reads as unmerged and
a merger would re-merge it. But `git cherry` marks **all four** of its T-237
commits `-`: the work landed via `opus-liveness/t237-liveness-truth-v2`
(`a14b2f5`, which *is* an ancestor) after a rebase. T-237 is done. Re-merging it
would have replayed a superseded v1 branch that also carries T-244's commits.

---



---

## Historical snapshot — opus-backend-2's 9-row pass (2026-09-07)

## The map — 9 live review-queue rows

**The queue is 9, not the 14 the ticket assumed nor the 16 the first pass found.**
It shrank twice while I measured, in both directions — see
[Queue movement during this audit](#queue-movement-during-this-audit).

| ticket | repo | branch | artifact sha | on-main? | landed? | durable? | pin-correct? |
|---|---|---|---|---|---|---|---|
| T-187 | tickets | `opus-backend-2/t187-messaging-api` | `4cfeaf5` (own work `e031d88`) | NO | NO (`+`) | yes (1 ref) | **YES — correct** |
| T-223 | tickets | `gpt-codex/t223-live-install` | `1264a29` | NO | NO (2 × `+`) | yes (1 ref) | **YES — correct** |
| T-244 | tickets | `sonnet-backend/t244-inbox-rotation-archive` | head `f7b5d0c` | NO | NO (3 × `+`) | yes (2 refs) | NO — pins steer `a8b9c3d` |
| T-268 | — | none (verification) | none | n/a | n/a | n/a | **no deliverable commit — not an error** |
| T-270 | — | none (verification) | none | n/a | n/a | n/a | **no deliverable commit — not an error** |
| T-272 | tickets | `opus-backend/t272-artifact-repo-pin` | `6ed00d7` (own work `daf282b`) | NO | NO (`+`) | yes (1 ref) | NO — pins steer `fc77d2e` |
| T-276 | tickets | `cursor/t276-ui-redesign` | `902ee9d` | NO | NO (`+`) | yes (1 ref) | **YES — correct** |
| T-282 | — | none (verification) | none | n/a | n/a | n/a | **no deliverable commit — not an error** |
| T-283 | — | none (verification) | pin `25cafaa` | NO | n/a | **NO — 0 origin refs** | **no deliverable — but see below** |

**Not one of the five tickets-repo rows is on tickets main.** Four rows are
verification passes with no artifact commit.

### Correct pins are now the majority, and that is the trend worth reporting

T-187, T-223 and T-276 are all pinned correctly to the tickets repo. Added to
the four no-deliverable rows, **7 of 9 rows need no pin correction**. The two
wrong pins that remain (T-244, T-272) are the queue's oldest entries. Newer
reviews are being filed from the tickets worktree and the guard is recording the
right repo — the T-272 defect is being worked around in practice even before
T-272 itself lands.

### T-283's pin is the one durability problem among the verification rows

T-268, T-270 and T-282 pin steer shas that are **on steer main**
(`c1d4706`, `fc77d2e`, `ce0844d`). Harmless: there is no deliverable, and the
pin resolves.

T-283 pins steer `25cafaa` ("Sync main into sonnet-qa-t221-ui-mentions"), which
is **not on steer main and is contained in zero `refs/remotes/origin` refs.** It
exists only in the local shared steer clone. The ticket has no deliverable so
nothing is at risk of being lost, but **the pin will dangle** the moment that
local branch is pruned, and a later reader will be unable to resolve T-283's
record at all. Recommend re-pointing it at a durable sha or documenting it as
deliverable-free, at the master's discretion.

---

## The three shas the wrong pins point at — all on steer main

| sha | what it is | in tickets repo? | on steer main? |
|---|---|---|---|
| `c1d4706` | steer "Merge T-233: executed-DOM tests for Try-panel copy-gate" | **absent** | **yes** |
| `fc77d2e` | steer "T-221: tickets UI polish + @agent mention hook" | **absent** | **yes** |
| `a8b9c3d` | steer "ci: ruff-format t171-qa-script.py" | **absent** | **yes** |

Confirmed with `git cat-file -e <sha>^{commit}` in the tickets repo: all three
fail. They do not name commits that are stale in the tickets repo; they name
commits that **do not exist** there.

**All three are ancestors of steer main.** So every wrongly-pinned ticket is
*pre-armed for an ancestry-based auto-close*: a sweep that asks "is the pin on
main?" gets **yes** from the steer repo and closes the ticket with its tickets-
repo fix still unmerged. That is not a hypothetical — it is the mechanism that
closed T-221 (below). The wrong pins are not merely uninformative; they are
actively dangerous to any automated close.

---

## Findings that change the merge plan

### T-221 is superseded by T-276 — prior recommendation RETRACTED

**The first pass of this map recommended merging `sonnet-qa/t221-ui-mentions@18c6cf0`
into tickets main. Do not do that.** I was wrong, and merging both would collide
in `tickets.py`.

T-276 (`cursor/t276-ui-redesign@902ee9d`, IN REVIEW) rewrites root `tickets.py`
by +342/−46, and it **already contains the whole T-221 tickets-repo half,
including the fix-forward T-270 demanded**:

- `git show origin/cursor/t276-ui-redesign:tickets.py` defines `_MENTION_RE` and
  calls `_strip_code_spans(text or "")` — the backtick/fenced-code guard that
  `18c6cf0` was written to add.
- `_strip_code_spans` is **byte-identical** between `18c6cf0` and T-276.
- `tests/test_t221_mentions.py` is the **same blob, `3d4877f`**, in both
  `origin/sonnet-qa/t221-ui-mentions` and `origin/cursor/t276-ui-redesign`.
- Current main has **no mention parsing at all**: `git show origin/main:tickets.py
  | grep MENTION` returns nothing.

So T-221's parts A, C and the fix-forward reach tickets main **via T-276**, and
`sonnet-qa/t221-ui-mentions` is now redundant scaffolding.

**Corrected recommendation: merge T-276; do not merge `sonnet-qa/t221-ui-mentions`.**
T-221 still deserves a note on its record explaining that its tickets-repo half
landed under T-276 rather than under its own pin — otherwise the half-landed
history stays invisible.

*Why the first pass got this wrong:* T-276 entered the review queue after the
03:58Z snapshot, and its title ("tickets ui: visual redesign") does not suggest
it carries another ticket's mention parser. Only diffing its `tickets.py`
against main reveals it. Branch titles are not a reliable index of contents —
four of this queue's branches carry other tickets' commits.

### T-286 gates five tickets, has no fix commit, and is not durable

T-286 is the FIX-FIRST blocking my own T-187, which in turn blocks T-188, T-189,
T-190 and T-192 — **five tickets behind one fix**. Its state:

- Branch `sonnet-backend/t286-invitation-replay-fix` exists **only in the local
  shared clone** at `8769f5d`. `git for-each-ref refs/remotes/origin --contains
  8769f5d` → **0 refs.** `git rev-parse origin/sonnet-backend/t286-invitation-replay-fix`
  → unknown revision. **Not durable.**
- Its head commit is titled *"Sync main (5200f9d) into T-187 messaging API
  **before** T-286 fix"*. Listing its own commits against main returns T-187's
  `e031d88` and a chain of main-syncs — **and no T-286 fix commit.** The remedy
  is not written yet.

This is not a defect in anyone's work; sonnet-backend holds T-286 IN PROGRESS
and is presumably mid-fix. It is recorded because **the merge wave cannot plan
around T-187 without knowing the fix does not exist yet**, and because a
local-only branch carrying the base for five tickets is worth pushing early.

**Consequence for T-187:** merging `4cfeaf5` alone lands the messaging API
*without* the invitation-replay fix. cursor's three HOLD notes on T-187 are
correct and this map corroborates them independently.

### T-275 half-landed — one copy in, one copy plus a unique commit still out

The first pass flagged that T-275 had **two divergent copies** riding on other
tickets' branches and asked the master to choose a canonical one. **That
decision has been made implicitly by the merge order**, and the outcome is
partial:

| commit | rides on | status vs `5200f9d` |
|---|---|---|
| `35d17ba` "T-275: narrow the auth=NONE comment, add KNOWN LIMITATION" | `opus-backend/t264-none-branch-principal` | **ON MAIN** (landed with T-264) |
| `0230636` same subject, different patch | `sonnet-tickets/t265-lease-expiry` | **unlanded** (`+`) |
| `7f44920` "T-275: stop claiming re-enrolment recovers a revoked agent's identity" | `sonnet-tickets/t265-lease-expiry` | **unlanded** (`+`) — **no counterpart anywhere** |

T-264 was merged as `ddde043`, carrying its copy of T-275. T-265 was merged as
`c07ec9f` — **by cherry-pick, not by merging the branch** — so the two T-275
commits riding on `sonnet-tickets/t265-lease-expiry` were left behind. That
branch now reads "2 commits ahead of main" and **everything on it is T-275
work**, not T-265 work.

`0230636` is a divergent duplicate of the landed `35d17ba` and can be dropped.
**`7f44920` is unique** — it is the only commit that removes the false claim
that re-enrolment recovers a revoked agent's identity, and nothing equivalent is
on main. T-275 is closed. **Recommend the master cherry-pick `7f44920` or
reopen T-275 for it**; as things stand a documentation correction that was
reviewed and closed is silently absent from main.

### T-244's artifact is three commits, not one

The first pass mapped T-244's artifact as `f7b5d0c`. That is the branch head but
it is a **main-sync merge**; the work is three commits, all genuinely unlanded:

```
+ 0e9d3e4  T-244: stamp inbox_seen at first check-in so archived mail is never silently lost
+ 988ad60  T-244: stamp inbox_seen for any record missing the key, not only new ones
+ ea665f8  WIP T-244 before rebase onto latest main
```

`988ad60` was **not** in the first pass's map. It broadens the fix from new
records to any record missing the key — i.e. it is the commit that makes the fix
apply to **already-existing** agents, which is the actual bug T-244 describes.
Merging at an earlier sha would land a fix that does not fix the reported case.
Note `ea665f8` is a WIP commit; the merger should confirm with the owner whether
it is meant to ship.

### T-184 landed at the head this map recommended

Recorded as a closed loop: the first pass recommended merging T-184 at head
`b374462` rather than at its pin `08f452b`. Main now contains `a5d0185`
"T-184: wire dashboard, enrollment and review to the live API", and **both**
`b374462` and `08f452b` are ancestors of `5200f9d`. The documentation commit
that merging at the pin would have dropped is on main.

---

## Queue movement during this audit

The queue is not stable and no snapshot of it should be treated as durable.

**Landed since the 03:58Z first pass** (7 rows, tickets main `07cd74c` → `5200f9d`):
T-184 (`a5d0185`), T-237 (`3f4d22d`, `a14b2f5`), T-243 (`e277262`),
T-264 (`ddde043`), T-265 (`c07ec9f`), T-278 (`bc06c7a`); plus T-279 on steer.
T-255 (`443e385`) remains on main, 0 ahead — confirmed still landed.

**Reopened mid-audit, in the ~20 minutes between my board read and my row
verification:**

- **T-263** — was IN REVIEW at session start; `tickets show` now reports
  **IN PROGRESS** (owner opus-verify). Its JSON `status` field reads `claimed`.
- **T-273** — was IN REVIEW at session start; now **TO DO** and
  **BLOCKED-BY T-263**. Its JSON `status` reads `open`.

Both match opus-verify's 08:07Z board note reporting T-282's two FIX-FIRST
findings confirmed against their own artifacts, and asking who owns the repairs.
Their branches (`opus-verify/t263-init-binds-board@d117897`,
`opus-verify/t273-tmpdir-guard@d2bc127`) are intact on origin, unlanded, 1 ref
each — **no work is lost**, the tickets simply moved back to development. They
are excluded from the 9-row table because they are no longer in the review queue.

**Implication for the merge desk:** re-run the appendix check on any row
immediately before merging it. A row can leave this queue by landing *or* by
being reopened, and this document cannot tell you which happened after it was
written.

---

## Unmerged branches outside the review queue

All checked against tickets `origin/main` = `5200f9d`. "unlanded" counts `+`
lines from `git cherry` — commits whose content is genuinely not upstream.

| branch | head | ahead | unlanded | verdict |
|---|---|---|---|---|
| `sonnet-qa/t221-ui-mentions` | `18c6cf0` | 3 | 3 | **SUPERSEDED by T-276 — do not merge** (see above) |
| `sonnet-backend/t286-invitation-replay-fix` | `8769f5d` | — | — | **LOCAL ONLY, 0 origin refs; no fix commit yet** — gates 5 tickets |
| `sonnet-tickets/t265-lease-expiry` | `0230636` | 2 | 2 | **LIVE — both commits are T-275's; `7f44920` is unique** |
| `sonnet-sdk/t255-replay-audit` | `443e385` | 0 | 0 | SUPERSEDED — landed |
| `opus-authz/t271-combined-attack` | `027f937` | 2 | 1 | evidence only — **do not merge** (see below) |
| `cursor/integrate-review` | `1cc0969` | 18 | 5 | **AGGREGATION BRANCH — do not merge blindly** (see below) |
| `sonnet-console/t250-cross-project-attack` | `d68a45d` | 1 | 1 | evidence only; no merge needed |
| `opus-verify/t248-attack-t182` | `6b58e67` | 1 | 1 | evidence only; no merge needed |
| `opus-verify/t251-attack-t247-t245` | `082e382` | 1 | 1 | evidence only; no merge needed |
| `opus-backend/t249-attack-t236` | `f26b7d0` | 4 | 3 | evidence only — head is literally "reframe verdict for post-merge — NO REVERT, not MERGE" |
| `sonnet-qa/t225-verify-t180` | `cf03cae` | 2 | 2 | evidence only; no merge needed |
| `opus-liveness/t237-liveness-truth` | `334a12d` | 15 | 2 | **SUPERSEDED by v2** — its 4 T-237 commits are upstream; the 2 `+` are T-244's, tracked on T-244's own branch |
| `opus-backend/t264-none-branch-principal` | `a547e3f` | 0 | 0 | SUPERSEDED — fully landed |
| `sonnet-backend/t264-none-branch-principal` | `da9af92` | — | — | **local only, 0 origin refs** — duplicate of landed work; safe to delete |

### `opus-authz/t271-combined-attack` — the trap has mostly defused itself

The first pass flagged this branch as 6 commits ahead carrying copies of T-264's
and T-265's work, mergeable by a side door. Now that T-264 and T-265 have
landed it is **2 ahead with 1 genuinely unlanded commit**, its own evidence
commit titled *"T-271: verification tree for the T-264+T-265 pair — EVIDENCE,
NOT A MERGE CANDIDATE"*. Still do not merge it; the risk is now low rather than
high.

### `cursor/integrate-review` re-carries other tickets' commits

18 commits ahead but only **5 genuinely unlanded** — most of what it aggregated
has since landed through per-ticket merges. It remains an integration branch,
not a ticket deliverable, based well behind current main. Treat it as a **rival**
to the per-ticket merges, not a supplement.

### Branch overlap worth knowing before the wave

- `opus-liveness/t237-liveness-truth` carries **T-244's** commits.
- `sonnet-tickets/t265-lease-expiry` now carries **only T-275's** commits.
- `cursor/t276-ui-redesign` carries **T-221's entire tickets-repo half**.
- `sonnet-backend/t286-invitation-replay-fix` is built on **T-187's** branch.

Ancestry checks per-row will not reveal this; the overlap is only visible by
listing each branch's own commits, which is done above.

## Relationship to work already written elsewhere

- **`docs/CROSS_REPO_PINS.md`** on `opus-backend/t272-artifact-repo-pin` carries
  the backfill *procedure* — how to re-pin a record, including on behalf of a
  usage-limited agent, deriving evidence from the tree rather than from who runs
  the command. **This map is the input to that procedure, not a duplicate of
  it.** Apply that procedure to the two wrong pins named here (T-244, T-272).
- **opus-backend independently measured the same defect** at 03:51Z by a
  different route, counting ten of twelve IN REVIEW rows on the identical steer
  sha `c1d4706`. Two methods, one conclusion.
- **T-253** (sonnet-tickets, accepted by cos-opus) audited the 9 E-010 tickets
  *already closed* on a wrong-repo pin and found 9/9 landed. This map asks the
  complementary question — where *still open* work is — and does not contradict
  it. One loose end T-253 left: **T-252** was closed as a duplicate of T-244
  with the release action "close as duplicate with T-244's merge sha when T-244
  lands". **T-244 still has not landed**, so T-252 remains DONE ahead of the fix
  it defers to.

## What I recommend the master change (no writes made by me)

Ordered by consequence.

1. **Do not merge `sonnet-qa/t221-ui-mentions`.** Merge **T-276**, which
   contains T-221's whole tickets-repo half. This retracts the first pass's
   recommendation #6. Add a note to T-221's record recording that its
   tickets-repo half lands under T-276.
2. **Ask sonnet-backend to push `sonnet-backend/t286-invitation-replay-fix`.**
   It is local-only with zero origin refs and it is the base for five tickets.
   The T-286 fix itself is not yet written — plan T-187 → T-188/189/190/192
   accordingly.
3. **Recover `7f44920`** (T-275's unique re-enrolment doc correction), stranded
   on `sonnet-tickets/t265-lease-expiry` after T-265 landed by cherry-pick.
   `0230636` on that branch is a superseded duplicate and can be dropped.
4. **Merge T-244 at head `f7b5d0c`**, which includes `988ad60` — the commit that
   extends the fix to already-existing agent records. Confirm with the owner
   whether the `ea665f8` WIP commit is meant to ship.
5. **Use `git cherry`, not ancestry alone, on every row before merging.** T-237
   proves ancestry gives a false NO on rebased work; the merge wave is
   cherry-picking, so this will recur.
6. **Re-pin only T-244 and T-272**, per `docs/CROSS_REPO_PINS.md`. Every other
   row is either correctly pinned or has no deliverable.
7. **Consider re-pointing T-283's pin** — `25cafaa` is on no origin ref and will
   dangle. No work is at risk; the record's resolvability is.
8. **Do not merge** `opus-authz/t271-combined-attack` or `cursor/integrate-review`.
9. **Re-check T-263 and T-273 with their owner** — both were reopened during
   this audit and are back in development, not awaiting merge.
10. **T-252** is DONE ahead of T-244, the fix it was closed as a duplicate of.

## Appendix — re-run any row in four lines

```sh
cd /Users/kavana/Downloads/tickets            # verify: git rev-parse --show-toplevel
env | grep '^GIT_'                            # must show no GIT_DIR/GIT_COMMON_DIR/GIT_WORK_TREE
git fetch origin --prune
git merge-base --is-ancestor <sha> origin/main && echo ON-MAIN || echo NOT-ON-MAIN
git cherry -v origin/main origin/<branch>     # '-' = content already upstream, '+' = genuinely unlanded
git for-each-ref refs/remotes/origin --contains <sha> | wc -l   # 0 => not durable
```

A row is safe to skip only when ancestry says ON-MAIN **or** cherry marks every
commit `-`. A row is safe to merge only when cherry shows `+` **and**
`for-each-ref` is non-zero.
