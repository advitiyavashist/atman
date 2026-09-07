# Organic post-FLAG shadow-vs-actual scorecard (T-445)

**Recommendation: INSUFFICIENT** — but *not* for the expected reason (`n<20`).
n is 27. The scorecard cannot yet answer the V1 question because of five
independent data-quality defects, each recorded below.

| field | value |
|-------|-------|
| Snapshot generated | `2026-09-07T23:37:57Z` |
| Live CLI sha (`tickets --version`) | `52f8eaff2d1637ec0c3b296b88fe30becb9228a4` |
| FLAG recut sha | `db6229d` (T-425); `merge-base --is-ancestor db6229d 52f8eaf` = YES |
| Doc written on | atman `origin/main` `3e7e82b`; `src/ticket_board/scheduler.py` byte-identical to `52f8eaf` (`git diff 52f8eaf origin/main -- src/ticket_board/scheduler.py` empty) |
| Board read | `/Users/kavana/Downloads/steer/.tickets` (live, read-only) |
| Reader | opus-verify — authored none of T-415 / T-416 / T-461 |

Turns are taken verbatim from `tickets turns --json`; the T-425 idle FLAG is
not reimplemented here. Every scorecard number below was cross-checked against
`tickets turns --json` and matched on all 27 rows (zero mismatches).

This is **observational evidence only** — not a counterfactual claim about what
would have happened had the shadow pick actually been assigned. No shadow pick
in this window ever ran the ticket it was picked for, so no row below contains a
measured comparison. (Framing carried forward from composer's `28d482c`, which
this document replaces; see *Provenance* at the end.)

## 1. Aggregate (post-FLAG rows only)

`tickets route --shadow --score` reports the two eras separately and never as
one pct (T-461 behaving as specified):

```
agreement pre-T-425 (idle review wakes counted): 0.08 (2/26)
agreement post-FLAG: 0.04 (1/27)
era counts: pre-T-425=26  post-FLAG=27 (not mixed into one pct)
```

Pre-T-425 rows are excluded from everything below, not mixed in.

| metric | value |
|--------|-------|
| n (post-FLAG, done, bound `run_start`) | **27** |
| agreement | **1/27 (0.04)** — the one agreement is T-463 |
| median turns, agree | **n/a (n=1)** — per T-461's own n<2 rule |
| median turns, disagree | 2.0 (n=26), range 1–6 |
| `cost_usd` measured | **0/27** (board-wide: the `cost_usd` key is absent on **all 463** `run_end` rows — never written once) |
| `cost_usd` null (UNMEASURED) | **27/27**, never substituted with 0 |
| `tokens_in` measured | 0/27 |
| rows with `outcome=limit` | **0/27** |
| decision source | `prior` on **27/27**; `learned` on 0 |
| shadow expected-turns median available | **0/27** (`shadow_med` = `-` on every row) |

Composer's draft on `composer/t445-organic-scorecard` printed
`median turns (agree) | 2 (n=1, T-463)`. That is a median at n=1, which is the
exact case T-461 was landed to suppress. It is reported as `n/a` here.

The brief's `outcome=limit` caveat (set by a log grep regardless of exit code)
does not bite on this dataset: **zero** of the 27 scored rows carry it, so no
row was excluded or relabelled on that account.

## 2. Per-ticket (post-FLAG, n=27)

`turns` from `tickets turns --json`. `live?` = did the shadow-picked agent emit
any trajectory event in the 60 minutes before this ticket's claim.

```
ticket   role     actual         shadow         agree turns  src    live?  last shadow-agent event before claim
T-391    research cursor-modal   -              no       1   prior  n/a    NO PICK
T-430    backend  composer       claude         no       4   prior  NO     NEVER (zero events in the whole log)
T-432    backend  composer       claude         no       4   prior  NO     NEVER (zero events in the whole log)
T-439    backend  grok-worker    claude-fable   no       6   prior  yes    2026-09-07T20:20:10Z
T-446    verifica grok-worker    opus-verify    no       2   prior  yes    2026-09-07T19:59:22Z
T-441    verifica cursor-demo    opus-verify    no       1   prior  yes    2026-09-07T19:59:22Z
T-440    infra    cursor-modal   ceo            no       3   prior  NO     2026-09-07T17:51:56Z
T-448    verifica composer       opus-verify    no       3   prior  yes    2026-09-07T19:59:22Z
T-449    verifica grok-worker    cursor         no       2   prior  NO     2026-09-07T17:50:32Z
T-454    verifica composer       opus-verify    no       2   prior  NO     2026-09-07T19:59:22Z
T-455    verifica composer       cursor         no       2   prior  NO     2026-09-07T17:50:32Z
T-456    verifica grok-worker    opus-verify    no       3   prior  NO     2026-09-07T19:59:22Z
T-457    verifica cursor-demo    opus-verify    no       1   prior  NO     2026-09-07T19:59:22Z
T-458    research composer       -              no       3   prior  n/a    NO PICK
T-461    backend  grok-worker    claude-fable   no       3   prior  yes    2026-09-07T21:17:31Z
T-462    verifica cursor-modal   opus-verify    no       1   prior  NO     2026-09-07T19:59:22Z
T-464    infra    cursor-modal   ceo            no       3   prior  NO     2026-09-07T17:51:56Z
T-389    backend  composer       sonnet-tickets no       6   prior  NO     2026-09-07T19:59:11Z
T-463    console  composer       composer       YES      2   prior  yes    2026-09-07T21:53:47Z
T-465    infra    cursor-demo    cursor-2       no       1   prior  NO     NEVER (zero events in the whole log)
T-466    verifica cursor-demo    opus-verify    no       1   prior  NO     2026-09-07T19:59:22Z
T-467    verifica composer       opus-verify    no       2   prior  NO     2026-09-07T19:59:22Z
T-468    verifica grok-worker    cursor         no       2   prior  NO     2026-09-07T17:50:32Z
T-417    infra    cursor-demo    cursor-2       no       1   prior  NO     NEVER (zero events in the whole log)
T-469    verifica composer       cursor         no       5   prior  NO     2026-09-07T17:50:32Z
T-472    infra    cursor-demo    cursor-2       no       1   prior  NO     NEVER (zero events in the whole log)
T-442    infra    cursor-modal   cursor-2       no       1   prior  NO     NEVER (zero events in the whole log)
```

## 3. Why the agreement rate is not yet evidence — five defects

### D1. The learned ranking never fired. All 27 rows are the fallback prior.
`source` is `prior` on 27/27 rows and `shadow_median` is null on 27/27.
`decide_ticket` returns `source: "learned"` only when `learned_ranking` finds a
`(role, band, agent, model)` cell with `n >= MIN_SUPPORT` (=5,
`scheduler.py:29`); otherwise it falls through to `prior_pick`
(`scheduler.py:312`). No cell ever reached n=5 at any claim time in this window.

**Consequence:** this scorecard has not tested T-415's turns-then-cost ranking
on a single row. `1/27` is the agreement rate of a static prior, not of the V1
router. Reading it as "V1 routing disagrees with us 96% of the time" would be
wrong — V1's ranking was never consulted.

### D2. 19 of 27 shadow picks were dormant seats; 6 picks have no history at all.
`prior_pick` scores agents from the *live* registry and never asks whether the
agent was running. Activity of the shadow-picked agent in the hour before claim:

| | rows | agreement among them |
|---|---|---|
| shadow pick was active | 6/27 | 1/6 |
| shadow pick was dormant | 19/27 | 0/19 |
| no shadow pick at all | 2/27 | — |

The result is not an artifact of the 60-minute horizon:

```
horizon    live-pick rows   agreement among them
15 min      3/27            1/3
 1 h        6/27            1/6
 3 h       14/27            1/14
24 h       19/27            1/19
```

Threshold-free: **6/27 rows** (T-430, T-432, T-465, T-417, T-472, T-442) name
an agent — `claude` or `cursor-2` — that has **never emitted a single
trajectory record**. `claude`'s agent record was last seen `2026-09-05T16:21`
and `cursor-2`'s `2026-09-06T22:13`, both before this window opened.

The shadow pick set `{opus-verify×9, cursor×4, cursor-2×4, claude×2,
claude-fable×2, ceo×2, sonnet-tickets×1, composer×1}` and the actual owner set
`{composer×10, grok-worker×6, cursor-demo×6, cursor-modal×5}` overlap in one
name. Near-zero agreement is structurally guaranteed by that disjointness, so
the rate measures registry staleness, not routing quality.

**This is a liveness artifact, not a role artifact.** The role half of the
candidate-set question is a clean negative and should not be reported as a
finding: all 27 shadow picks have a `roles.json` entry and every pick matches
its ticket's role — `ceo`=[review, infra, coordination] on infra rows,
`cursor`/`cursor-2`=[verification, acceptance(, infra)] on verification and
infra rows, `opus-verify`=[verification, backend] on verification rows,
`sonnet-tickets`/`claude`=[backend] and `claude-fable`=[backend, review] on
backend rows, `composer`=[backend, evals, console] on the console row. The
known hole at `cli.py:3179` — the role gate is skipped when `roles.get(name)`
is `None` — never fired here, because 0/27 picks lacked an entry. The role gate
worked.

What is missing is any liveness gate at all. `score_agent`
(`cli.py:3171-3194`) gates on `needs`/`can`, roles, cost, priority and
`best_for` keywords, and contains zero references to `seen`, `limit`, `alive`,
`down` or `heartbeat`; `prior_pick` adds only `_matches_prior`. Nothing in the
candidate set asks whether an agent is running or limited. `claude` is not even
in the workforce — it enters the candidate set purely from the `DEFAULT_ROLES`
table, with no workforce entry and no execution history.

### D3. Non-picks are scored as disagreements.
`agree = bool(actual and shadow and actual == shadow)` (`scheduler.py:661`) and
the denominator is `len(rows)`. T-391 and T-458 produced no shadow pick and are
counted as wrong picks. Excluding them: 1/25 rather than 1/27. Small in size,
but it means the denominator answers "how often did the shadow name the actual
owner", not "how often was the shadow's pick right".

### D4. The T-425 FLAG is inert on 100% of the scored data.
`_run_is_productive` (`turns.py:178`) is meant to count a run only if that
`run_id` wrote the bound ticket. Its second branch reads:

```python
rid = end.get("run_id")
if not rid:
    return True  # pre-T-425 jsonl: every completed run counted
```

Live trajectory records carry `run_no` (an int), not `run_id`. Measured on the
live log:

- **0 of 66** `run_end` records across the 27 scored rows carry a `run_id`.
- Board-wide, **18 of 161** post-FLAG `run_end` records carry one; the first is
  `2026-09-07T22:57:33Z` (cos-opus). Only the Claude seats respawned at ~23:00Z
  emit it. The Cursor-harness seats — composer, cursor-demo, cursor-modal,
  grok-worker — are the actual owners of **all 27** scored rows and emit none.

So every scored row takes the `return True` fallback and counts every completed
run, which is exactly the pre-T-425 behaviour. Reported turns for these rows
total 66, which equals the `run_end` count exactly. Using run start/end windows
as a diagnostic proxy for the FLAG's intent (an approximation, *not* a
reimplementation — no number above is derived from it), only **41 of 66** runs
wrote anything to their bound ticket, and **14 of 27 rows** carry a turns
number higher than their writing runs.

**Consequence:** the `post-FLAG` era label (T-461) is accurate about *when* a
row happened and says nothing about *which turns rule produced its number*. All
27 are counted pre-FLAG-style. This is a live defect in the FLAG's binding key,
not in T-461's labelling.

*Update (cos-opus, 2026-09-08, after this snapshot):* the Cursor-harness seats
have since started emitting `run_id` — 9 cursor `run_end` records carry one,
first at `23:20:49Z`, from composer and grok-worker; board-wide 23 of 169
post-FLAG `run_end` now carry one. D4's remediation is partly landing on its
own. The numbers in this section are left at their `23:37:57Z` snapshot values
so every one of them stays traceable to the row that produced it. Fix ticket:
**T-481**.

### E1. The era label's own safety net is dead, exactly like the FLAG.
*Found by cos-opus on the independent verification pass, not by this scorecard's
author. Fix ticket: **T-483**.*

`scheduler.row_era` decides a row's era from the first bound `run_start`'s
release sha and falls back to that `run_start`'s wall-clock timestamp only when
the sha is unknown; `_run_start_sha` reads `ev['sha']` or `ev['pin']`. Measured
across the whole log: **0 of 468** `run_start` records carry either field — the
union of keys ever written on a `run_start` is `agent`, `at`, `harness`,
`harness_cmd`, `kind`, `model`, `objective_id`, `run_id`, `run_no`, `ticket`,
`trigger`, `v`, `worktree`. So `_sha_is_post_flag` returns `None` every time and
the era is decided **purely by wall clock**.

E1 has a **second half, also measured rather than inferred**, contributed by
cos-opus from the live watch loops (each seat's resolved release sha is visible
in its argv). Even if `run_start` *did* record a sha, the allow-list it would be
checked against is stale: of the six distinct release shas actually executing on
this box, four are unrecognised by `scheduler._PRE_FLAG_PIN_PREFIXES` /
`FLAG_PIN` / `FLAG_FEATURE_PIN`, and the two that are recognised are classified
**pre-FLAG** — cursor-modal, optimizer and cursor-fable on `21ca63c`, cursor-demo
on `1c8335b`, all four owning a large share of the scored rows. **Not one running
seat is on a pin the module recognises as post-FLAG**: `db6229d` and `705dd05`
are executing nowhere. So `_sha_is_post_flag` would return `None` for every live
seat regardless.

**This changes the fix shape for T-483:** stamping the sha on `run_start` (half
i) is *not sufficient on its own* — the era would still fall through to the clock
for most seats because of half (ii). Whoever takes T-483 must fix both, or
replace the hand-maintained pin allow-list with something that does not need
editing every release.

That compounds D4 rather than duplicating it, because the sha branch exists for
exactly the situation this board was in: the FLAG landed at `20:37:13Z` but the
seats did not restart onto it until roughly `23:00Z`, so for about two and a
half hours runs executed on a *pre-FLAG* release at a *post-FLAG* timestamp.
With the sha branch inert those runs are labelled post-FLAG on time alone, while
D4 shows their turns were counted pre-FLAG-style because the binding key is
absent from their records. The two defects are the same hole seen from both
ends: **"post-FLAG" in this scorecard is a statement about the clock, not about
which turns rule produced the number.** This is the single strongest reason the
gate reads INSUFFICIENT, because it disqualifies the era split itself — the one
thing T-461 was built to protect. (T-461 is still behaving as specified: it
prints two era lines and never one pct. The defect is in what feeds it.)

### D5 (minor). `--score` has no machine-readable output.
`tickets route --shadow --score --json` prints the fixed-width text table;
`--json` is honoured only for `--shadow` without `--score`. Every downstream
number must be hand-transcribed from a padded table, which is how a median at
n=1 reached a draft recommendation.

## 4. Rule 8 — one disagreeing ticket, argued from its trajectory

**T-469** (verification; actual `composer`, shadow `cursor`, disagree, 5 turns).
Chosen because it is the only post-FLAG row where the actual owner's turns (5)
markedly exceed that owner's own comparable median (`actual_med 2.0(n=4)`) — on
its face the strongest case that routing cost us turns.

Its complete event stream:

```
22:26:06Z claim      composer
22:26:09Z run_start  composer   (run_no 63)
22:28:23Z review     composer   + msg
22:28:32Z run_end    composer   exit=0 duration_s=143
22:39:49Z run_start  composer   (run_no 65)   22:40:11Z run_end  22s
22:46:12Z run_start  composer                 22:46:38Z run_end  26s
22:49:39Z run_start  composer                 22:50:00Z run_end  21s
22:53:00Z run_start  composer                 22:53:20Z run_end  20s
23:13:03Z done       cursor-fable
```

The work finished on the first run: claimed 22:26:06, review submitted
22:28:23 — **2m17s, one run**. The four later runs (22:39–22:53, 20–26s each)
wrote **nothing at all** to T-469: no update, no msg, no status change. They are
post-review idle wakes while the ticket sat waiting for the desk, and they are
counted as 4 of its 5 turns only because these records carry no `run_id` (D4).

**Verdict: not decidable, and for a reason stronger than "no counterfactual
exists."** Two things would each independently have to be fixed first:

1. The comparison has no other side. `cursor` has `shadow_med = -` — no
   comparable finished tickets at claim time, so there is no expected-turns
   number to compare 5 against. This holds on all 27 rows, not just this one.
2. The quantity being compared is not executor effort. 4 of T-469's 5 turns
   measure how long the *desk* took to merge, not how the *executor*
   performed. On the executor-effort reading, T-469 took 1 turn — better than
   composer's own median of 2, i.e. the disagreement points the opposite way
   from what the raw number suggests.

`cursor` had also been dormant since 17:50:32Z, 4h35m before the claim, so the
shadow pick could not have executed this ticket at all.

### Also carried forward: composer's rule-8 row (T-430)
The document this replaces argued rule 8 on a *different* disagreeing ticket, so
that argument is preserved here rather than dropped. T-430 (`backend`, actual
`composer`, shadow `claude`, 4 turns): the shadow pick came from the historical
prior for `claude` on backend tickets; `composer` completed it in 4 turns.
Comparable finished backend tickets for `claude` had insufficient n for a median
at claim time (`-` in the scorecard), so whether `claude` would plausibly have
finished in fewer than 4 turns is **not decidable** from the trajectory alone —
no `claude` run on T-430 exists. The disagreement is a routing-prior mismatch,
not an observed turns delta.

Two independently chosen rows, two `not decidable` verdicts, for the same
structural reason: on all 27 rows the shadow pick has no comparable-turns median
to compare against. T-430 additionally sits in the 6/27 group whose shadow pick
(`claude`) has never emitted a single trajectory record (D2).

## 5. Recommendation

**INSUFFICIENT.**

n is 27 post-FLAG rows, so the anticipated "n<20" justification does not hold —
the sample is large enough. The scorecard is nevertheless unreadable as an
evidence gate for V1 capability+cost+task routing, for five reasons that are
each sufficient on their own: the learned ranking fired on **0/27** rows so
T-415 is untested (D1); **19/27** shadow picks name a dormant seat and **6/27**
name an agent with no execution history at all, making the 1/27 agreement a
measurement of registry staleness (D2); the cost axis of "fewest turns at least
cost" has **0/27** measured values — and board-wide the `cost_usd` key is absent
on all 463 `run_end` rows, so it has never been written once, while 14 of them
do carry a non-null `tokens_in`: tokens flow and cost never has; the turns axis
is counted by the pre-T-425 rule on **27/27** rows because no live record
carries the `run_id` the FLAG binds to, inflating **14/27** rows (D4); and the
era split that is supposed to contain all of this is itself decided by wall
clock alone, because **0 of 468** `run_start` records carry the release sha
`row_era` looks for (E1) — so "post-FLAG" on every row above is a claim about
when the row happened, not about which turns rule produced its number.

Do not start the V1 routing ticket on this evidence.

**The blocker is data quality, not sample size.** Growing n cannot resolve this
gate: the n≥20 threshold is already met at n=27 (29 by the next morning), and
every defect above is invariant to n. Any forward path phrased as "once n is
large enough" is void.

What has to be fixed before this scorecard can be read as an evidence gate, each
with the ticket that owns it:

| # | needed | ticket |
|---|--------|--------|
| 1 | `run_id` on Cursor-harness `run_end` (or a different FLAG binding key) so the T-425 FLAG actually applies to the seats doing the work (D4) | **T-481** |
| 2 | A release sha stamped on `run_start`, or `row_era` refusing to label rather than falling through to wall clock (E1) | **T-483** |
| 3 | A liveness/limit filter in the shadow candidate set, plus an agreement line computed over live candidates only (D2) | **T-484** |
| 4 | Harness cost capture on the seats that do the work. T-480's read-time list-price estimate is the right shape but **cannot be back-applied** to these 27 rows — they stay UNMEASURED by name (D1 cost axis) | **T-480** |
| 5 | A live-CLI recut onto the fixes in (1)–(3), so later rows are measured under the corrected rules — and, per E1 half (ii), so that the seats are actually *running* a release the era logic recognises | **T-486** |
| 6 | Re-read the corrected instrument and issue the GO/NO-GO | **T-470** (re-scoped; see below) |

**T-470 was void as originally written, and has since been re-scoped.** Its
first framing was "the n≥20 GO/NO-GO … once cost rows exist" — void, because n≥20
was already met at this snapshot and no amount of additional n touches any defect
above. It now reads "GO/NO-GO for V1 routing on ORGANIC POST-RECUT rows (T-486
epoch): agreement all-rows **and live-candidate-subset**, turns delta with n, cost
UNMEASURED/est by name (T-480), one disagreement argued from the trajectory", and
is gated `BLOCKED-BY T-486, T-482`. That is the right shape: it reads a *recut*
epoch rather than a bigger sample of the same broken window, and the
live-candidate-subset line answers D2 directly. Recorded here so the correction
is not re-litigated.

A **role** fix is *not* needed, and should not be filed: the role gate passed on
27/27 (see D2). Reporting the 1/27 agreement as a wrong-role finding would be
wrong.

These are observations for the planner, not changes made here.

No scheduler, router, route or assign code was changed by this ticket or by the
one that produced it. The only file touched is this document.

## Provenance — why this file was replaced, not merged

Two same-path artifacts were produced for T-445: composer's `28d482c` (80 lines)
and this one, `opus-verify/t445-organic-scorecard@0e59cdc` (270 lines). The desk
fast-forwarded `28d482c` onto `main` at 2026-09-07T23:51:10Z; the optimizer's
HB124 and cos-opus's independent verification pass — both of which read this
document and told the desk to take it instead — arrived afterwards. T-482 is the
correction, and T-445 stays DONE (INSUFFICIENT is the right word on both).

This is a **replacement, not a union**: a union of the two files would leave the
`median turns (agree) | 2 (n=1, T-463)` line — a median at n=1, the exact case
T-461 was landed to suppress — sitting next to its own correction. The two items
that `28d482c` carried and this document did not (its rule-8 argument on T-430,
and its "observational evidence only" framing) have been carried forward above,
with attribution, so the replacement loses nothing.
