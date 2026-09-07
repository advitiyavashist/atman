# E-010 artifact map — where the review queue's work actually lives

**Ticket:** T-281 · **Author:** opus-backend-2 · **Snapshot:** 2026-09-07T03:58:55Z

This document resolves every review-queue pin to its real repo, branch and sha so
the merge wave can run off evidence instead of off the recorded `commit` field.
It changes nothing. No merge, no fast-forward, no force-push, and no `tickets
review` was re-run to "fix" a pin — re-pinning from the wrong cwd is the defect
(T-272), not the remedy. Corrections are **named here and left for the master.**

## Provenance of every command in this document

Per T-243, cwd alone does not establish which repo a git command answers for.

- Ambient git environment checked before the first git command:
  `env | grep '^GIT_'` returned only `GIT_EDITOR=true`. **No `GIT_DIR`,
  `GIT_COMMON_DIR` or `GIT_WORK_TREE` was set**, so cwd-based discovery is
  trustworthy in this session.
- Every "tickets repo" command below ran with
  `git rev-parse --show-toplevel` = `/Users/kavana/Downloads/tickets`.
- Every "steer repo" command below ran with
  `git rev-parse --show-toplevel` = `/Users/kavana/Downloads/steer`.
- Board records were read from `/Users/kavana/Downloads/steer/.tickets/T-*.json`
  (read-only; nothing under `.tickets/` was edited).

Trunks resolved after a fresh `git fetch origin --prune` in each repo:

| repo | trunk at snapshot |
|---|---|
| `advitiyavashist/tickets` | `origin/main` = **`07cd74c`** "T-255: check preconditions after the replay guard in submit_review/decide_review" |
| `advitiyavashist/steer` | `origin/main` = **`fc77d2e`** at 03:58Z snapshot; **moved to `ce0844d` at 04:02Z** during verification (forward move) |

### The trunk moved twice while I was measuring — read this before trusting any row

1. My first fetch resolved tickets `origin/main` = `d615c1a`. Minutes later the
   same ref read `07cd74c`. This is a **forward** move, not a rewind:
   `git merge-base --is-ancestor d615c1a origin/main` → **true**. A merge wave is
   running live in the shared clone.
2. `origin/opus-backend/t264-none-branch-principal` moved from `35d17ba` to
   `a547e3f` mid-session (sonnet-sdk pushed T-274's fix onto it).

**Consequence for the merge wave:** every `on-main?` answer below is true as of
`07cd74c`. Rows can only move from NO to YES, never back, *provided* main
continues to move forward. Re-run the two-line check in the appendix before
merging any individual row.

## Method — three questions per row, not one

- **on-main?** — `git merge-base --is-ancestor <sha> origin/main`, and nothing
  else. Checking the commit out and seeing it resolve proves nothing: tickets
  main was force-rewound earlier today and orphaned commits check out perfectly
  clean. "exists" and "is on main" are different questions.
- **durable?** — `git for-each-ref refs/remotes/origin --contains <sha>`. A
  commit reachable only from the shared local clone is one disk failure from
  gone. `is-ancestor` cannot see this and neither can a clean checkout.
- **pin-correct?** — the question is **"is this ticket's deliverable where its
  pin says"**, *not* "is this pin a steer sha". Those give different answers on
  five rows below, in both directions.

## The three shas the queue is pinned to

| sha | what it is | in tickets repo? | on steer main? |
|---|---|---|---|
| `c1d4706` | steer "Merge T-233: executed-DOM tests for Try-panel copy-gate" | **absent** | yes |
| `fc77d2e` | steer "T-221: tickets UI polish + @agent mention hook" (= steer main tip) | **absent** | yes |
| `a8b9c3d` | steer "ci: ruff-format t171-qa-script.py" | **absent** | yes |

Confirmed with `git cat-file -e <sha>^{commit}` in the tickets repo: all three
fail. They do not name commits that are stale in the tickets repo; they name
commits that **do not exist** there.

---

## The map — 16 review-queue rows

The queue is **16 deep, not 14**. T-237 and T-243 entered after the ticket was
written, and both are pinned correctly to the tickets repo — bringing the count
of not-wrong pins to five, not three.

| ticket | repo | branch | artifact sha | on-main? | durable? | pin-correct? |
|---|---|---|---|---|---|---|
| T-184 | tickets | `opus-console/t184-live-wiring` | head `b374462` | NO | yes (1 ref) | **repo/branch right, sha stale** |
| T-187 | tickets | `opus-backend-2/t187-messaging-api` | `4cfeaf5` | NO | yes (1 ref) | **YES — correct** |
| T-237 | tickets | `opus-liveness/t237-liveness-truth` | `334a12d` | NO | yes (1 ref) | **YES — correct** |
| T-243 | tickets | `opus-infra/t243-git-env-leak` | `efd1c2a` | NO | yes (1 ref) | **YES — correct** |
| T-244 | tickets | `sonnet-backend/t244-inbox-rotation-archive` | `f7b5d0c` | NO | yes (1 ref) | NO — pins steer `a8b9c3d` |
| T-263 | tickets | `opus-verify/t263-init-binds-board` | `d117897` | NO | yes (1 ref) | NO — pins steer `c1d4706` |
| T-264 | tickets | `opus-backend/t264-none-branch-principal` | head `a547e3f` | NO | yes (1 ref) | NO — pins steer `c1d4706` |
| T-265 | tickets | `sonnet-tickets/t265-lease-expiry` | `0230636` | NO | yes (1 ref) | NO — pins steer `c1d4706` |
| T-266 | tickets | `infra-2/t266-hooks-dedup-scope` | `45631d8` | NO | yes (2 refs) | NO — pins steer `c1d4706` |
| T-268 | — | none (verification) | none | n/a | n/a | **no deliverable commit — not an error** |
| T-270 | — | none (verification) | none | n/a | n/a | **no deliverable commit — not an error** |
| T-271 | — | `opus-authz/t271-combined-attack` (evidence only) | `027f937` | NO | yes (1 ref) | **no deliverable commit — see trap below** |
| T-272 | tickets | `opus-backend/t272-artifact-repo-pin` | `6ed00d7` | NO | yes (1 ref) | NO — pins steer `fc77d2e` |
| T-273 | tickets | `opus-verify/t273-tmpdir-guard` | `d2bc127` | NO | yes (1 ref) | NO — pins steer `c1d4706` |
| T-275 | tickets | **none of its own** — rides T-264 and T-265 | `35d17ba` / `0230636` | NO | yes | NO — **and there is no branch to merge** |
| T-279 | **steer** | `sonnet-tickets-t279-ruff-format` | `ec2ca28` | **YES (steer main, as of 04:02Z)** | yes | **repo right, branch+sha wrong — but now landed** |

**Not one of the 15 tickets-repo rows is on tickets main.** The single row that
has landed, T-279, is steer work and landed on *steer* main. Every artifact that
has been pushed is durable (present in at least one `refs/remotes/origin` ref);
the two exceptions are called out below and are **not** durable.

---

## The five rows where "steer sha" is the wrong reading

### T-279 — the pin is *not* simply "right", and the row landed mid-audit

The brief lists T-279 as a correct pin because it is genuinely steer work. The
repo half is right. The branch and sha are not:

- Recorded pin: `sonnet-tickets@fc77d2e`, repo `advitiyavashist/steer`.
- Real artifact (from sonnet-tickets' own review note): steer branch
  `sonnet-tickets-t279-ruff-format` @ **`ec2ca28`**, PR #112.

**This row changed while I was verifying it, and the change is instructive.**

- At the 03:58Z snapshot, steer `origin/main` was `fc77d2e` and
  `git merge-base --is-ancestor ec2ca28 origin/main` → **false**. The fix was
  unmerged, while the *pinned* `fc77d2e` was already steer main's tip — meaning
  any ancestry-based auto-close would have marked T-279 done with the fix still
  out and CI still red.
- At 04:02Z, re-running the same check against a freshly fetched steer repo:
  steer `origin/main` = **`ce0844d`** ("T-279: ruff format the 3 pre-existing
  unformatted files so CI verify stops false-redding"), and `ec2ca28` **is** now
  an ancestor. PR #112 landed between the two measurements.

So the outcome is benign — **T-279 is genuinely done and needs no action** — but
it arrived there by merge, not by the pin being right. Had the sweep run in the
four-minute window before the merge, the wrong-sha pin would have closed it for
the wrong reason. The pin is still not the artifact; it simply stopped mattering.
Recorded here rather than quietly dropped, because the near-miss is the finding.

### T-268 and T-270 — correctly *have* no honest pin

Confirmed: no branch matching `t268` or `t270` exists on tickets origin. Both
tickets' deliverables are verdicts filed as board notes on the tickets they
reviewed (T-258/T-261/T-224 and T-181/T-217/T-221/T-255 respectively). Their
steer pins are artefacts of the review command, not claims about a deliverable.
**Nothing to merge, nothing to correct.**

### T-271 — a verification ticket that *does* have a branch, which is a trap

Unlike T-268 and T-270, T-271 has `opus-authz/t271-combined-attack` @ `027f937`
on origin, 6 commits ahead of main. **Do not merge it.** Its own head commit is
titled *"T-271: verification tree for the T-264+T-265 pair — EVIDENCE, NOT A
MERGE CANDIDATE"*, and the other five commits are copies of T-264's and T-265's
work (`e78fc39`, `bfdbbb1`, `ff6b1c6`) assembled to test them together. Merging
this branch would land the T-264/T-265 pair by a side door, bypassing the merge
order T-271 itself prescribes. T-271's deliverable is its verdict, like T-268
and T-270; the branch is scaffolding.

### T-184 — right repo, right branch, sha behind the head

The pin `opus-console/t184-live-wiring@08f452b` is the only queue pin that names
the tickets repo *and* a real tickets-repo sha, so it reads as correct. It is
not the branch head. `08f452b` is an ancestor of head `b374462`, and one further
T-184 commit sits after it:

```
f88e5cf  T-184: document the V1 token-serving boundary and the production seam
08f452b  T-184: wire the dashboard, enrollment and review to the live API   <-- pinned
```

Merging at the pin silently drops the documentation commit. Merge the branch
head `b374462`, not the pin. (The brief's hand map lists `f88e5cf`; that is the
last *own* commit, but the branch head is the later sync merge `b374462`.)

### T-187 — my own ticket, mapped like any other

Pin `opus-backend-2/t187-messaging-api@4cfeaf5`, repo `advitiyavashist/tickets`.
`git rev-parse origin/opus-backend-2/t187-messaging-api` → `4cfeaf5`. Pin sha
**equals the branch head**; repo and branch both correct. On-main: NO (6 ahead,
3 behind). Durable: 1 origin ref. This pin is correct — recorded here with the
same three checks used on every other row, and with no verdict attached.

---

## Two rows that are worse than a wrong pin

### T-275 has no artifact of its own, and its work exists in two divergent copies

This is the most consequential finding for the merge wave.

- `sonnet-tickets/t275-reenrollment-route` exists **only in the local shared
  clone**, at `82ed639`. There is no `origin/` copy:
  `git rev-parse origin/sonnet-tickets/t275-reenrollment-route` → *unknown
  revision*. **Not durable.**
- That local branch is **0 commits ahead of main** (`git rev-list --count
  origin/main..` → `0`), its worktree is clean, and `82ed639` is itself an old
  main commit ("T-224: amend E-010 contract per T-211 mismatch map"). The branch
  is its own base. **There is nothing on it.**
- T-275's actual work is committed on **two other tickets' branches**, and the
  two copies are **not the same change**:

| commit | rides on | diffstat |
|---|---|---|
| `35d17ba` "T-275: narrow the auth=NONE comment, add KNOWN LIMITATION on create_enrollment" | `opus-backend/t264-none-branch-principal` | `app.py` +30 −13 |
| `0230636` same subject | `sonnet-tickets/t265-lease-expiry` | `app.py` +18 −15 |

`git patch-id --stable` differs between them, and `git range-diff` confirms they
are not the same patch. `origin/sonnet-tickets/t265-lease-expiry` additionally
carries `7f44920` "T-275: stop claiming re-enrolment recovers a revoked agent's
identity".

**Merging T-264 and T-265 both — which the queue intends — lands two divergent
edits to the same region of `src/ticket_board/server/app.py`.** They will
conflict, or worse, apply cleanly in a way that half-reverts one of them. The
master must decide which T-275 copy is canonical *before* the wave, not during
conflict resolution. This is not visible from any single ticket's notes.

### T-264's duplicate branch is local-only and undurable

T-271 flagged that T-264 has two branches carrying the same fix. Resolved:

- `opus-backend/t264-none-branch-principal` — **on origin**, head `a547e3f`
  (moved from `35d17ba` during this session; now also carries T-274's
  `_authorize` 4-arg fix and T-275's `35d17ba`). This is canonical.
- `sonnet-backend/t264-none-branch-principal` — **local only**, `da9af92`,
  containing `52b42cc` "T-264: auth=NONE never resolves a principal…".
  `git for-each-ref refs/remotes/origin --contains da9af92` → **0 refs**. It is
  not on origin at all and is one disk failure from gone.

Merge the `opus-backend` branch. Note that doing so now also lands T-274 and one
copy of T-275 — see above.

---

## Unmerged branches outside the review queue

All checked against tickets `origin/main` = `07cd74c`.

| branch | sha | ahead | status | verdict |
|---|---|---|---|---|
| `sonnet-qa/t221-ui-mentions` | `18c6cf0` | 3 | T-221 **done** | **LIVE — work never landed, see below** |
| `sonnet-sdk/t255-replay-audit` | `443e385` | **0** | T-255 done | **SUPERSEDED — landed**, it is main's tip commit |
| `sonnet-console/t250-cross-project-attack` | `d68a45d` | 1 | T-250 done | evidence only (attack sweep); no merge needed |
| `opus-verify/t248-attack-t182` | `6b58e67` | 1 | T-248 done | evidence only; no merge needed |
| `opus-verify/t251-attack-t247-t245` | `082e382` | 1 | T-251 done | evidence only; no merge needed |
| `opus-backend/t249-attack-t236` | `f26b7d0` | 4 | T-249 done | evidence only — head commit is literally "reframe verdict for post-merge — NO REVERT, not MERGE" |
| `sonnet-qa/t225-verify-t180` | `cf03cae` | 2 | T-225 done | evidence only; no merge needed |
| `gpt-codex/t223-live-install` | `ea80443` | 4 | T-223 **claimed** (infra-2) | **LIVE** — in-flight work for an open ticket, do not merge ahead of its owner |
| `cursor/integrate-review` | `1cc0969` | 18 | — | **AGGREGATION BRANCH — do not merge blindly**, see below |
| `cursor/harden-tickets-clear-8bfe` | `e33dcfd` | — | not in the ticket's list | unreferenced by any queue ticket; ask cursor before touching |

### T-221 is closed DONE and its work is not on tickets main

T-221's board record reads `status: done`, pinned to
`cursor/cos-s05-wave-d83a@a8b9c3d` — a **steer** sha. T-221 was delivered across
**both** repos, and only one half landed.

Its steer half **is** on steer main: `6e78332` "T-221 part B: @mention support in
Cursor + Codex board hooks" and `fc77d2e` "T-221: tickets UI polish + @agent
mention hook". That is very likely why the ticket reads as delivered.

Its tickets-repo half is not. In the tickets repo:

- `09d6b33` (part A, mention parsing), `2c1eaff` (part C, composer/autocomplete)
  and `18c6cf0` (the fix-forward T-270 required) are each **not** an ancestor of
  tickets `origin/main`.
- `git log origin/main --grep=T-221` returns **nothing**. Main has no T-221
  commit at all.

So a shipped, verified, twice-reviewed feature — sonnet-backend re-verified the
fix-forward MERGE-READY at 03:53Z — is marked delivered with its steer half on
steer main and **parts A, C and the fix-forward still off tickets main**. A
half-landed ticket is exactly what a single-repo ancestry check cannot see: the
pin resolves, the sha is on *a* trunk, and the ticket closes. It is outside the
review queue, so no merge-wave row will pick up the remainder.
**Recommend reopening T-221 or merging `sonnet-qa/t221-ui-mentions@18c6cf0`
into tickets main explicitly.**

### `cursor/integrate-review` re-carries other tickets' commits

18 commits ahead, and its contents include `45631d8` (T-266's fix), `2c1eaff`
and `09d6b33` (T-221 parts A and C) and `0e9d3e4` (T-244's fix). This is why
`45631d8` is the one artifact sha in two origin refs rather than one. It is an
integration branch, not a ticket deliverable: merging it would land T-266, T-221
and T-244 in one opaque step, out of the order the verification tickets
prescribe. Treat it as a **rival** to the per-ticket merges, not a supplement —
and note it is 18 commits ahead but based well behind current main.

### Branch overlap worth knowing before the wave

- `opus-liveness/t237-liveness-truth` carries `0e9d3e4` and `ea665f8` — **T-244's
  commits**. T-237 and T-244 overlap; merging both needs care.
- `opus-backend/t264-none-branch-principal` carries T-264 **+ T-274 + T-275**.
- `sonnet-tickets/t265-lease-expiry` carries T-265 **+ two T-275 commits**.
- `opus-authz/t271-combined-attack` carries copies of T-264 **and** T-265.

Four of the queue's branches carry each other's work. Ancestry checks per-row
will not reveal this; the overlap is only visible by listing each branch's own
commits, which is done above.

## Reconciliation with the earlier T-253 audit

T-253 (sonnet-tickets, accepted by cos-opus) audited the 9 E-010 tickets closed
on a wrong-repo pin and found **9/9 landed**. I did not re-walk those rows and
this document does not contradict that result — T-253 asked whether *already
closed* work reached main, this one asks where *still open* work is. Its method
(use the ticket's own notes and branch field, never the auto-recorded `commit`
field; confirm with `is-ancestor` against the correct repo's trunk) is the same
method used here. Note T-253 resolved against tickets main `1ce5fbb`; main is
now `07cd74c`.

One loose end it did not cover: T-252 was closed as a duplicate of T-244 with
the explicit release action *"close as duplicate with T-244's merge sha when
T-244 lands"*. T-244 has not landed — it is row 5 of this map, unmerged — so
T-252 is currently DONE ahead of the fix it defers to.

## What I recommend the master change (no writes made by me)

1. **T-279 needs no action — it landed at 04:02Z** (steer main `ce0844d`). Kept
   in the map because until 03:58Z it was the one row a sweep could have closed
   for the wrong reason: its pin `fc77d2e` was already an ancestor of the trunk
   its fix was waiting on.
2. **Decide which T-275 copy is canonical** (`35d17ba` on T-264's branch vs
   `0230636` on T-265's) before merging either branch. They are different
   patches to the same file.
3. **Push or discard the two local-only branches** —
   `sonnet-tickets/t275-reenrollment-route` (empty anyway) and
   `sonnet-backend/t264-none-branch-principal` (`da9af92`, real work, 0 origin
   refs). The second is genuinely at risk.
4. **Merge T-184 at head `b374462`, not at its pin `08f452b`.**
5. **Do not merge `opus-authz/t271-combined-attack` or `cursor/integrate-review`.**
6. **Raise T-221** — done and verified, steer half landed, tickets half (parts
   A, C, fix-forward) unmerged and tracked by nothing.
7. Correct pins are T-187, T-237, T-243 (tickets repo) and T-268, T-270 (no
   deliverable). T-271 is a sixth "leave it alone" row of a different kind.

## Appendix — re-run any row in two lines

```sh
cd /Users/kavana/Downloads/tickets            # verify: git rev-parse --show-toplevel
env | grep '^GIT_'                            # must show no GIT_DIR/GIT_COMMON_DIR/GIT_WORK_TREE
git fetch origin --prune
git merge-base --is-ancestor <sha> origin/main && echo ON-MAIN || echo NOT-ON-MAIN
git for-each-ref refs/remotes/origin --contains <sha> | wc -l   # 0 => not durable
```
