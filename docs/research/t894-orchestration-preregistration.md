# T-894: cheaper coordinator versus one frontier worker

Status: proposed pre-registration; design only. CEO owns disposition and CoS
runs the frozen pilot when capacity permits. No model job was launched.

## Hypothesis and source boundary

The operator's source is [Anshu's post](https://x.com/anshuc/status/2098811738674147520)
and [linked article](https://x.com/i/article/2098775543843893248). Both returned
403 during this review; targeted search found no accessible copy. The claims
recorded on T-894, including its reported savings, task count, and author-judged
quality, are the ticket's account of that article, not independently verified
facts. Archive an authorized copy if available; do not reconstruct it.

**Testable hypothesis:** on bounded UI/docs work, a cheaper coordinator doing
scope management and screenshot QA reduces total measured or explicitly
estimated resource use, without reducing independently accepted outcomes or
introducing unacceptable quality, accessibility, delay, or human intervention.
Do not optimize a coordinator's spend while hiding its worker/reviewer spend.

This pilot tests a routing package, not whether a particular model is smarter,
nor whether Atman beats manual work. Different QA placement and messages are
part of the treatment. A later factorial study would separate those effects.

## Arms and immutable run settings

| Setting | A: Fable alone | B: cheaper coordinator + Fable |
|---|---|---|
| Implementation | One continuous Fable worker session | One continuous Fable worker session |
| Planning/QA | Worker handles its own bounded planning and browser/source QA | One fixed cheaper coordinator handles scope and QA; worker implements |
| Initial worker information | Frozen task brief, scoped source snapshot, fixture and screenshots/reference when applicable | Exactly the same information; coordinator forwards the brief without extra architectural narrative |
| Corrections | Independent review findings; worker self-QA | The same independent findings plus coordinator's bounded QA findings |
| Limits | At most three worker rounds | At most three worker rounds; one coordinator assessment per round |
| Independent gate | Same blinded evaluator and checks | Same blinded evaluator and checks |

Resolve Fable to its actual provider/model/version before enrollment. Fix the
worker harness, reasoning setting, permissions, tool/browser versions, context
policy and account tier across a pair. Fix one coordinator model/harness for
the whole pilot: prefer an available lower-cost Claude model only if its known
rate or reported spend supports that label. Sonnet is not automatically cheaper
because of its seat title. If Haiku/Sonnet/Cursor/Agy are substituted, start a
separately registered stratum; do not pool it as the same treatment. Both arms
must use the same worker harness, so provider differences do not masquerade as
orchestration improvements.

Continuous means reuse the worker's original conversation through correction
rounds, not start a fresh frontier CLI for each feedback message. A session
restart is recorded as a continuity failure and its cost remains attributed.
There is no paid standing CEO/CoS inside either arm. CoS launches and collects;
Sol advises; neither coaches implementations or supplies an extra approval.

## Matched tasks and sampling

Pilot: **three task pairs, six attempts**, one pair per family below. This is
an instrumentation/routing pilot, not a statistically powered savings claim.

| Family | Frozen task | Objective acceptance |
|---|---|---|
| Atman app polish | A fixture-driven Work header/detail slice showing objective, Done when, a named dependency blocker, and next eligible ticket/teammate | Correct for working, waiting, held, capture, reserved-only and no-ready fixtures; keyboard selection/detail navigation; 390px and desktop layout; light/dark readability; no implied wake from reservation |
| Steer landing | A bounded existing action-example section, not a site redesign | Synthetic allow/redact/block examples with coherent policy/evidence/action and agent next step; no compliance guarantee, fabricated measured score or unsupported SDK command; responsive and keyboard-accessible interaction |
| Atman README | A fixed-length introduction and first-win/contribution section | A fresh reader can identify team runtime/BYOA, separate identities, objective/dependency handoff and independent review; commands succeed in a clean fixture using the pinned CLI; philosophy is brief, capabilities honest, no research/product conflation |

CoS selects and records historical or current source baselines **before any
attempt**. Use the same baseline SHA, dependencies, files, fixture, brief and
acceptance checks within each pair. Exclude already solved versions or artifacts
that disclose the implementation. The evaluation copy must not contain the
other arm's patch, conversation or live board inbox. Isolate worktrees, worker
IDs, hooks and writable paths using already accepted mechanisms; do not make
this pilot an identity/wake engineering experiment. Work only on sandbox
artifacts; production owners keep their tickets and branches.

Validate that each brief is feasible within scope using a human inspection of
the baseline and acceptance checks. Do not spend an uncounted model run solving
it first or discard difficult tasks after seeing results. Sample all three
families, not just the one likely to favor screenshots. Randomize A/B order
within each pair using a recorded seed; run pairs sequentially, with no shared
mutable build output or cross-arm context. Never run all A then all B. Freeze
the model/account state; quota/backend drift is a recorded confound.

## Round definition, review and terminal success

A round is one authorized bounded worker work episode ending in a candidate
artifact or the attempt deadline. An episode can contain multiple model/API
turns and tool calls: a three-round cap is **not** three model turns. Record
actual turns only when the harness supplies them. No invisible worker subagents,
remote model calls, unbounded background jobs or coordinator delegation.

After each submitted candidate, collect its source/build/check evidence and
standardized screenshots for UI. The worker in A or coordinator in B may report
bounded QA findings; neither may declare independent success. A blinded
reviewer assesses the candidate against the frozen gate. Stop on ACCEPT;
otherwise provide the same review findings to the arm for its next round.
Coordinator feedback may add only specific scoped defects, not new objectives.
Reviewer correctness/accessibility findings are never withheld to favor an arm.

Use one evaluator assigned before runs; strip arm/model/author labels from
candidate presentations. Give it the same tools, checklist and rubric. For UI,
inspect the same viewport/theme/state captures and keyboard flow; docs need
source/command checks rather than a meaningless screenshot score. Record any
unavoidable unblinding. Ideally a second independent reader codes the rubric;
if unavailable, label it single-reviewer evidence. The evaluator is outside
implementation and may not use an arm's self-grade as its verdict.

Terminal pilot success is **independent ACCEPT of the exact artifact SHA plus
passing objective checks** within the cap. It is `accepted_noncode` for the
sandbox comparison, not `accepted_merge`, production DONE or deployment.
If a patch is subsequently reused, normal exact-artifact independent review,
target-main verification and release gates still apply. Experimental review
does not auto-merge anything. Failed/pending attempts stay in the cohort.

## Metrics, labels and evidence

Reuse the T-853/T-811/T-831 definitions; store linked episode records rather
than inventing a second dashboard or inferring model turns from board pulses.

| Measure | Predeclared definition |
|---|---|
| Accepted outcome | Per-arm attempts with independent exact-artifact ACCEPT and all hard checks / all three enrolled attempts; failures and deadline-limited attempts included |
| First-review ACCEPT | ACCEPT on first independent candidate review / all enrolled attempts; no submission is a failure for this whole-cohort measure; also report observed submissions and verdict coverage |
| Rework | Number of linked independent FIX verdicts and subsequent correction rounds; separately count self/coordinator QA findings and issues resolved, not commits or repeated comments |
| Resources | Worker + coordinator + reviewer tokens by input/output/cache categories when supplied; linked harness-reported USD; estimated API-equivalent USD separately, with pinned rates and assumptions; unknown fields null |
| Time | Start of authorized attempt to terminal outcome or deadline; active work, coordinator, review and transport waits where known. Sum sequential total elapsed, never overlap durations. No-success attempts have observed age and censored time-to-ACCEPT |
| Turns/overhead | Observed model turns and tools separately from bounded worker episodes and productive paired watch runs. Count all coordinator calls/messages, unchanged-state acknowledgements, scope resets and restarts. Missing turn coverage is a lower bound |
| Human intervention | Setup outside enrollment reported separately; after start, operator unblocking, auth/login, clarification, manual wake, restart, scope rescue and edits counted by reason; independent scheduled review is separate |
| Quality | Blinded 0–2 rubric below, hard objective checks, accessibility findings and post-acceptance verification defects; no self-reported “looks good” success |

Rubric: six dimensions, scored 0 = fails, 1 = partly meets, 2 = meets with
evidence: (1) task/first-screen clarity, (2) factual/evidence honesty,
(3) information order and concise language, (4) brand/product consistency,
(5) readability and navigation, (6) scoped completeness. For docs, dimension 5
means heading/link/code readability and successful first-win instructions.
For UI it includes tested keyboard and viewport/theme behavior. Record rationale
per dimension. **ACCEPT requires all task-specific hard checks, no dimension
0, and at least 10/12.** Speculative visual taste alone does not override an
objective failure. Rubric/gate changes after enrollment invalidate confirmation
and require a new version, not retrospective favorable relabeling.

Cost labels: `reported`, `estimate`, `partial_observed`, `unmeasured`. A flat
subscription's incremental cash charge may be zero while its quota/resource
use is not; subscription fee, remaining balance, tokens, and API-equivalent
price remain separate. Never call estimated API-equivalent cost actual savings.
If one billed call has both tokens and USD, do not count both as dollars.
Allocate shared reviewer/setup coordination with a frozen rule: dedicated work
to its attempt, common setup split equally across six attempts, unrelated
launch work excluded. Unallocatable relevant spend stays visible as unallocated
and prevents a fully attributed total-cost claim. Partial sums are lower bounds.

Required join/provenance fields: experiment/version and registration digest;
task/pair/arm; sandbox ticket/runtime/session IDs; repo/baseline/brief/fixture
digests; model/harness/effort/account billing provenance without secrets;
episode and message IDs; start/end/candidate/review timestamps; candidate SHA;
gate outputs, screenshots and verdict/reviewer; cost/token sources, missing
coverage, interventions, stop reason. A self-contained export must replay the
same table as of a fixed cutoff. Do not publish private transcripts or auth data.

## Budget and stop rules

- Maximum **30 minutes elapsed per attempt**, including coordinator and review;
  six attempts, maximum **180 minutes aggregate enrolled elapsed**. At most
  three worker rounds and one bounded coordinator QA assessment per round.
  Timeouts/no-submission are failures, not free missing rows. Limits are pilot
  ceilings, not permission to occupy launch-critical seats for three hours.
- Before start, CoS records an account-approved allowance and a numeric
  observed-token or estimated-USD admission budget with its coverage/source.
  Apply the same total budget per attempt, including coordination/review.
  Unknown actual spend is not $0 and Atman admission estimates are not hard
  provider spend enforcement. Use existing authorized limits; absent a usable
  limit, keep the pilot unlaunched rather than invent one.
- Start only when the accepted runner and spare capacity exist within the
  current cap. B needs a worker and coordinator simultaneously; reserve review
  capacity without breaching that cap. On launch contention, stop scheduling
  new attempts and checkpoint an active attempt under existing policy. Record
  capacity preemption; publish an incomplete pilot, never silently drop it.
- Auth/quota/tool interruption after start consumes time and observed spend.
  Mark the infrastructure reason; retain the attempt in the primary table.
  At most one separately labeled replacement pair may be approved before
  inspecting comparative quality, solely for a verified infrastructure failure;
  original attempts remain shown. No retry until a favored arm wins.
- Any unsafe permission demand, identity collision, cross-arm leakage,
  destructive/out-of-scope change or unsupported wake claim stops the attempt.
  Correctness/security/auth/identity/wake/verification lanes remain excluded
  from cheap-model ownership. A scoped UI that reports evidence is allowed;
  repairing the underlying enforcement is not.
- Stop the pilot at the earlier of completed six attempts, registered account
  allowance exhaustion, launch contention, or **2026-09-16 18:00 +08**. No
  launch ticket depends on the experiment. No interim savings-based stopping,
  automatic router change or escalation into a benchmark platform.

## Analysis and decision rule

Publish every attempt and per-pair deltas, never only accepted examples.
Report accepted counts, first-review outcomes, rubric dimensions, time/stop
reason, rounds, interventions and resource coverage side by side. Ratios require
nonzero comparable denominators and fully attributed costs for both arms; no
ratio when either cost is unknown. Cost/accepted outcome is undefined at zero
acceptances, not zero. When acceptance differs, do not describe cheaper failure
as greater efficiency. Show raw paired results and range; with three pairs,
uncertainty is large and success-rate differences are not conclusive. Do not
claim significance or use a bootstrap on three pairs to suggest precision.

Candidate routing recommendation for a **follow-up pilot only** requires B
to have no fewer accepted attempts, no new hard-check failures, no pairwise
rubric decrease greater than 1/12, no extra rescue intervention, and comparable
resource/time coverage. Among pairs where both ACCEPT, target at least **20%
lower aggregate reported total cost** (or explicitly named API-equivalent
estimate), with no pair's elapsed delay above 20%. Failure of a threshold is
“do not adopt from this pilot,” not proof orchestration can never work.
Unknown cost allows a quality/instrumentation finding, not a savings decision.

No universal routing or public efficiency claim follows from six attempts.
If promising, register a separate confirmation on at least twelve **new**
matched task pairs spanning the same families, with its budget and uncertainty
analysis set before launch. Twelve pairs are a follow-up floor, not a power
guarantee. Choose sample size using the pilot variance and an explicit desired
precision; preserve held-out tasks. Until then permissible wording is “We are
testing cheaper coordination on bounded UI/docs tasks”; report measured pilot
counts privately with their missing coverage and limitations.

Result row template (no fabricated defaults):

| Pair / arm | Model / harness | Outcome / exact artifact | First verdict | Rubric / hard checks | Rounds / FIX | USD reported / estimated / coverage | Tokens / coverage | Elapsed / censoring | Interventions | Stop reason |
|---|---|---|---|---|---|---|---|---|---|---|
| Not run | Not enrolled | Not run | null | null | null | null | null | null | null | Not launched |

## Does our leadership layering create the alleged overhead?

There is a real risk in our CEO/CoS/planner/worker layering: the board already
contains repeated reachability acknowledgements, ownership reconciliation and
stale-pin follow-ups, and T-853 defines coordination cost separately for exactly
that reason. That is evidence of avoidable coordination events, not measured
proof of the article's cost claim or a reason to replace correctness reviewers
with cheaper models. Keep CEO at objective/scope/release decisions, CoS at
changed-state dispatch and blocker resolution, planner at bounded strategy,
and one worker at implementation. No second approval hop or paid status-only
pulse. This pilot should charge every necessary coordination action and stop
at its accepted artifact, instead of letting optimization become another
open-ended leadership project.

## CoS execution handoff

1. CEO accepts or edits this protocol. CoS freezes a registration manifest
   before results exist: exact snapshots/briefs/gates/digests, six attempts,
   order seed, models/settings, evaluator, account allowance/budget, cutoff.
2. Add a bounded run ticket after protocol acceptance and an independent
   analysis ticket after that run. Do not create six permanent standing bots,
   block launch, or mutate implementation owners. Reuse accepted runner/evidence
   export mechanisms. Seat/context isolation is a precondition, not new research.
3. Execute sequential matched pairs under capacity limits, collect original
   records and preserve all failures/preemptions. Pilot work ends at the stated
   terminal criteria; normal production PR handling is separate.
4. Analysis returns the complete table, limitations, and exactly one of
   **no adoption / instrumentation repair needed / register confirmation**.
   CEO decides routing; CoS closes the bounded run/analysis tasks with evidence.
