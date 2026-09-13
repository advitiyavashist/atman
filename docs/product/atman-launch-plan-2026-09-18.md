# Atman launch plan · September 18

Owner: Sol (`sol-planner-codex-0913`). Decision owner: Opus CEO. Execution and blocker owner: Sonnet CoS. T-853; board snapshot September 13, 2026, 10:05 Singapore time. Proposed scope and graph edits below are not applied by this document. The operator's revised objective supersedes the September 16 options in T-852.

## Promise and first win

**Bring the agents you already use into one working team.** Give them an objective, separate identities, a shared work graph, messages and a review path. See what is waiting, what is working and what actually finished.

The first win is a small, real delivery loop: join two distinct seats; plan A → B; finish and independently accept A; observe B start without a second operator instruction; inspect B's inherited handoff and final reviewable artifact. An explicit task can start a task-only seat; ordinary messages cannot. A continuous seat can act on an authorized message. The graph never turns a failed, held or merely submitted change into permission to release a dependent.

Atman's philosophy is concrete: an agent's intelligence is useful when it receives the right context for the work in front of it. Preserve each seat's identity, give it a bounded task and relevant decisions, and leave a verifiable handoff when it finishes. A successor inherits the work rather than repeating discovery. The ticket graph records execution; team knowledge records reusable decisions and artifacts. They are connected layers, not interchangeable stores. KV/latent research is not a launch dependency or a product claim.

Use Claude Code, Codex, Cursor, then Grok/custom remote in client examples; Agy gets its actual adapter path. This is not a Claude-only first seat. Advertise native, supervised and bridge transports separately. “Persistent” describes lifecycle, not proven reachability or unlimited autonomy.

## Lessons to adopt, adapt or reject

These are product decisions informed by sources, not claims of parity or Atman performance. Reuse [the existing Stripe/Ramp receipt](../research/stripe-minions-ramp-inspect.md); its permission-bypass analogies do not authorize bypassing Atman's review or host permissions.

| Source and evidence | Decision | Concrete `atm` / app behavior | Existing owner/ticket |
|---|---|---|---|
| Stripe Minions: unattended work ends at a reviewable PR; deterministic checks surround the agent loop. | Adopt the reviewable endpoint and bounded verification. | Each engineering node has acceptance criteria, exact candidate SHA and independent verdict. “In review” is visibly distinct from “Done”; authoring a PR does not release gated children. | T-781, T-810, T-812 |
| Ramp Inspect: verification uses the engineering environment, and sessions can be inspected across clients. | Adapt verification and inspectable state. | Artifact/check links on a node; browser evidence for UI work; one board identity across CLI/app. Reconnect shows actual enrolled runner and recovery action. | T-687, T-810, T-832 |
| Stripe/Ramp hosted machines, proprietary integration stacks and one-company agent loops. | Defer hosted fleets; reject copying their harness into Atman. | Local app and existing runners first; isolate worktrees. Vercel public product site does not expose the operator's private board or masquerade as a live hosted team. No blanket permission bypass. | T-824, T-819, T-832 |
| Cursor Projects: a responsive coordinator delegates execution, maintains reusable project context and subscribes to work signals. | Adopt role separation, inherited artifacts and event-driven work. | CEO owns objective/decisions, CoS removes blockers, Sol scopes strategic questions, workers implement. Show objective, graph, team and channels as one app. Targeted messages carry useful context and distinct delivery/work evidence. | T-810, T-818, T-819 |
| Cursor scaling research: flat coordination caused contention/drift; an extra integrator role became a bottleneck. | Adapt bounded ownership; reject another mandatory approval desk. | One implementation owner, one independent verifier and one named merge executor. Workers resolve their own conflicts. Batch delayed messages; no paid “unchanged” pulse. Atomic claim remains necessary; do not replace it with informal file coordination. | T-848 guidance; T-824, T-828, T-839 |
| Google scaling study: coordination helps some decomposable tasks and harms some sequential ones. | Adopt task-dependent parallelism; reject “more agents is better.” | Parallelize independent UI/docs/metric work; keep authorization and identity gates explicit. Compare task families with equal success criteria before claiming better routing. | T-811, T-831 |
| Operator's “graph engineering post.” | Source unresolved at this snapshot; no attributed claim yet. | Real dependency edges and success-trigger evidence already have operator requirements and shipped T-781/T-791 evidence. Do not invent a URL or restaff those implementations. CEO asked for original artifact; append confirmed source and any additional behavior once identified. | T-853 source follow-up |

Primary sources: [Stripe Minions](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents), [Ramp Inspect](https://builders.ramp.com/post/why-we-built-our-background-agent), [Cursor Projects, September 10](https://cursor.com/blog/projects), [Cursor scaling research, January 14](https://cursor.com/blog/scaling-agents), [Google scaling study](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/). Cursor Projects and scaling posts and Google source checked September 13. Stripe/Ramp details reuse the September 11 receipt. Vendor throughput and cost figures are not Atman metrics. Older local roadmap wording against “task graph” on the landing is superseded by the operator's explicit visible-work-graph requirement; keep public language simple: “See what starts next.”

Source distinction: existing T-806 acceptance contract calls its Cursor input “Cursor Chief of Staff Operational Rules & Evidence” (local board guidance), not an external architecture article. The two primary Cursor posts above are separately verified relevant references; they are not asserted to be the operator's original notes. Bounded artifact lookup found no unambiguous graph-post URL. CEO clarification requested on T-853; this is the only unclosed source-attribution item, not a reason to block already specified implementation.

## What the board actually says

| Work | Snapshot | Launch implication |
|---|---|---|
| T-806 acceptance / T-807 onboarding / T-801 missing-data lock | DONE | Build from these contracts. Targets and proposed adapters are not automatically measured capability. |
| T-781 success-trigger backend | DONE (`d88a4ca`) | Reuse, then independently verify live child work. A task message/receipt is not proof of a running child. |
| T-791 Work graph UI | DONE, reported on main `5373cbf` | Reuse graph payload/UI. T-796 is its BLOCKED duplicate; do not reopen or count it as a second deliverable. |
| T-809 primary `atm`, compatibility `tickets` | IN REVIEW, old PR #113 `a8a48f3` | Rebase and verify current source + installed wheel before merging. One implementation and one board. |
| T-828 transfer / T-839 identity boundary | IN REVIEW; independent T-846 ACCEPT `b47e7cc`, T-847 ACCEPT `7fbae42` | Integrate exact corrected candidates with preserved gates. Old PR #108 / superseded PR #114 pins are not the accepted changes. |
| T-824 explicit cross-repo spawn | IN PROGRESS | Verify repo + base + unique hook identity together. Do not spawn Atman work from a Steer-derived worktree. |
| T-687 connect/reconnect | TO DO, reserved identity not yet proven live | CoS staffs one actual Atman console worker. This is a real dependency of remote acceptance. |
| T-818 remote work start | TO DO, gated by T-687/828/830/846 | Freeze scopes and states, integrate transport, then test from separate client. Resolve current role holder, never hard-code retired CEO ID. |
| T-850 seat migration / T-851 Claude settings | IN REVIEW | Sol identity migrated. Stale global Claude hooks removed; real CoS enrollment still lacks native endpoint. File repair and `woken` transport receipt are not idle work-start acceptance. |
| T-811 scorecard / T-831 independent audit | IN PROGRESS / gated | Seven T-811 provenance defects are FIX. No public efficiency comparison until corrected event-level baseline is independently accepted. |
| T-810 app / T-819 OSS surface / T-832 release / T-812 final acceptance | TO DO, serialized | Proposed parallel cuts below; final release still requires all promised behavior. |

Fetch current refs and inspect current board before executing a merge or assignment; these are evidence snapshots, not durable leases.

## Proposed launch graph

Solid edges below are the proposed required inputs. This is a plan, not a CLI mutation. Existing completed contracts are omitted for readability. Final acceptance includes UI integration, documentation, live remote/auth, identity, explicit repo spawning and the independently audited metric baseline.

```mermaid
flowchart LR
  P[T-809 atm parity] --> U[T-810 coherent app]
  P --> D[T-819 README and entry]
  G[T-791 graph plus T-781 trigger: landed] --> U
  A[T-687 connect and reconnect] --> W[T-818 live remote acceptance]
  I[T-828 plus T-846 accepted transfer] --> W
  S[T-830 permissions contract] --> W
  B[T-839 plus T-847 identity isolation] --> F[T-812 final acceptance]
  R[T-824 target repo and unique hooks] --> M[T-831 independent metric audit]
  C[T-811 corrected scorecard] --> M
  U --> V[T-832 reviewed deployment and local verification]
  W --> V
  V --> F
  W --> F
  D --> F
  M --> F
  R --> F
```

T-810 can implement from frozen onboarding/permissions/metric schemas while T-818 and T-831 finish. It must integrate their accepted implementations before final browser acceptance. T-832 can prepare deployment and public previews early, but remains gated for its final remote/version smoke verdict. Do not relabel a static preview as the complete product.

### Ticket edits for CEO approval and CoS execution

1. **T-819: drop T-818 as a start dependency; retain T-809.** README preparation does not require remote work to be finished. Capability labels and exact commands must reflect evidence; final T-812 checks them against live remote behavior. Current T-784 documentation predecessor is already merged. Scope root cleanup to the primary contribution path; do not delete historical evidence or foreign branches.
2. **T-810: drop T-819 and T-831 as start dependencies; retain T-809 and frozen contracts.** Reuse landed T-791/T-781 and T-801 unknown lock. Add explicit integration checklist for T-687/818 and accepted metric data rather than serialized implementation. Keep T-831 in T-812 release gates. Do not disable missing functionality and then declare final app success.
3. **T-796: retain duplicate/remake status pointing to DONE T-791.** Fold any UX revamp into T-810. No new graph backend or planner.
4. **T-818/T-812: replace retired CEO name and September 16 deadline with current role-resolved runtime and September 18 objective.** First live slice targets the current CEO harness; Codex Sol remains a required independent supported-harness case. A displayed queued message does not pass. T-851 enrollment limitation stays on the live acceptance checklist.
5. **T-812: update stale reservation to one independent current verifier; retain security/identity/spawn/metric/doc/deployment dependencies.** Real transport matrix must name tested client/version, active versus idle, native/supervised/bridge and an observed ACK + work action. No universal Grok-native claim from a local wrapper unit test. Add T-830 accepted authorization and T-687 recovery acceptance explicitly to checklist; they remain transitively gated through T-818.
6. **T-811/T-831: use metric/label contract below.** Repair original seven defects; don't create seven competing scorecards. Relocate accepted implementation/artifact to Atman, pin source snapshots/digests, audit each KPI independently. Present per-KPI missing coverage rather than delaying UI layout.
7. **T-832: retain final T-810 and T-818 gates.** Prepare Vercel assets/link checks in parallel; record URL → SHA, local board evidence and rollback before DONE. Neither a deploy URL nor a clean git merge alone completes the app promise.

Do not apply these from this doc with blind numeric-ID rewiring: CoS checks current edges, actor ownership and later CEO decisions first. No duplicate implementation tickets needed. T-773/T-774 remain HOLD; learned router, KV research, hosted fleets and backend language migration are not this release's critical path.

## Product and UX acceptance

The first screen answers: “What are we trying to finish?”, “What is blocking it?”, “Who is doing the next step?” Objective and terminal success criteria sit above the Work graph. Each node shows real status, owner, waiting-on reason and artifact/verdict; selecting it reveals acceptance criteria and the handoff. A completed parent shows why its child became ready and whether that child actually began work.

Team separates unique runtime ID from stable role and displays lifecycle, provider/model when known, enrolled runner, auth state and last useful action. Channels show recipients, access scope and evidence-backed message state. “Queued” has a reason and recovery action. Do not manufacture “acknowledged” from inbox reads or “working” from heartbeat. Keep “Send message” and “Assign task” distinct, with visible task-only/continuous semantics and safe default permissions.

Metrics show measured counts, windows and unknown coverage beside the number. Empty/new boards show — and a useful first action, not fabricated charts. Preserve Atman's established visual identity rather than copying Steer. Prefer a graph and compact panels over an endless marketing scroll; keyboard-accessible list alternative, reduced motion, readable contrast and mobile follow-up are acceptance cases. This is a scoped product review, not a claimed live browser inspection.

Suggested README lead: “Atman brings your existing AI agents into one team: a shared objective, a work graph, messages and review, with a separate identity for every agent.” Follow with an executable install → `atm` join → app → A → B example, transport support matrix, short philosophy, architectural map and contribution path. Fewest-turns is an optimization objective; measured improvements require evidence. No localhost-only public CTA, unproved five-minute guarantee, research section or compliance promise.

## Efficacy metrics and labels v1

This extends existing [turns](../turns.md), [trajectories](../trajectories.md) and [coordination/success](../onboarding/coordination-and-success.md) semantics without renaming their schema-v1 keys. T-801 unknown lock, T-811 source corrections and T-831 independent verification remain authoritative. Required linkage not present in a source is a proposed instrumentation gap, never an inferred event.

### Common measurement rules

- Pin a half-open UTC window `[start,end)`, board/release identity, immutable source snapshots and digests, parser/contract version and export command. Reconstruct outcomes as of cutoff; later mutable ticket status cannot leak into a prior week.
- Pin eligible cohort at start or enrollment event. Report eligible, observed, missing, excluded with reasons, still-open and matured-by-deadline separately. Missing required linkage does not silently shrink the eligible denominator. Undefined ratios are null. Never turn unknown into success, failure or zero spend.
- Split harness, model/effort, execution context, task family/size and transport; unknown fields stay unknown. Provider/harness cost and productive board runs are separate dimensions. Historical backfill cannot establish native model turns or remote work start.
- One engineering work unit meets its criteria, has independent exact-SHA ACCEPT plus required checks and verified target-main content. Repairs/retries/extra PRs are attempts on that unit. Deployment and live objective completion are separate labels. Post-merge defects revise reliability evidence without erasing the historical merge event.
- Every KPI exports event IDs/source locations for numerator and denominator, missing links and label provenance. Reviewer-coded outcomes pin rubric, coder and adjudication; textual absence is not a label. Freeze measurement definition before looking for a favorable rate.

### Nine core KPI contracts

| KPI | Unit, eligible denominator and positive evidence | Missing-data and failure rule |
|---|---|---|
| Activation | New unique seat enrollment sessions; activated if identity/role, runner/auth, objective briefing, message check and first permitted bound-task action all link to that session. Report within-five-minute rate plus time distribution where timestamps exist. | `join` or heartbeat alone isn't activation. Five minutes is a proposed benchmark target, not measured promise. Missing step evidence = unmeasured; elapsed inactive sessions remain open/expired, not excluded. |
| Time to first claimed ticket | Enrolled eligible worker sessions with a ready permitted task; elapsed enrollment → its first valid claim. Report ready-at-join and later-ready cohorts separately. | Leadership seats/no-ready-task sessions are explicitly ineligible; auth-paused and eligible nonclaimers stay visible. No closed-only median sold as whole-cohort speed. |
| Directed-message delivery / ACK / work | Unique authorized directed messages/tasks to enrolled targets within a predeclared observation horizon. Separate durable enqueue, client delivery, explicit message-linked ACK and task-linked agent work start. Report idle/active and transport separately. | Inbox access, empty inbox, transport receipt or unrelated activity isn't ACK/work. Pending/not-due and expired/failed distinct. Zero-keystroke acceptance records operator intervention count; retries share one idempotency unit. |
| Task completion | Frozen claim cohort and maturity horizon; independently accepted work-unit merge by cutoff for engineering, rubric-defined terminal proof for other work. Report objective completion separately. | “Review” and DONE without required artifact/gate aren't engineering success. Still-open cohort, cancelled/excluded reasons and missing verification remain visible. Capture conversion/maturation, not only finished tasks. |
| Productive runs to accepted unit | Existing T-425/T-481 productive paired watch-run count from first claim through accepted merge, including repairs; publish comparable completed-cohort median and missing count. | One board run is not one underlying model turn. Nonproductive paid runs don't count here but their known cost/time belongs in overhead. Complete coverage required for total-turn comparison; partial observed count is a lower bound. |
| Elapsed time | First valid claim → accepted outcome; separately enrollment/queue, active execution, auth/offline pause, review and merge waits where interval events exist. | Missing components null; overlapping parallel durations aren't summed as wall time. Open tasks show age/right censoring, not made-up completion time. |
| Cost | Harness-reported cost for linked worker/retry/reviewer/CEO/CoS runs; accepted units per observed dollar and full cost/unit only on fully attributed cohorts. Show coordination separately and include it in total. | Missing actual cost null. Observed partial cost is a lower bound with coverage. Token × pinned rate is a labeled estimate, subscription plan spend is distinct. Shared coordination uses a declared allocation or stays unallocated; no arbitrary fallback USD. |
| Review rework | Units with observed first review-entry events and a linked independent verdict: first-review ACCEPT rate, FIX cycles and post-merge defect count. | Every DONE isn't a reviewed unit. Missing verdict/candidate linkage unmeasured; exclude nonreview work explicitly. One correction cycle can have several commits, not several successes. |
| Avoided rediscovery / inherited handoff | Successor task episodes receiving a specific earlier artifact/decision; reviewer rubric verifies correct reuse without repeating the relevant discovery, with outcome evidence. | Absence of “rediscover” or presence of a link isn't proof. No artifact/task linkage or no coded rubric = unmeasured. Show knowledge-use coverage separately; paired comparison needed for avoided-cost claims. |

### Labels and replay requirements

Persist or export only supported evidence with explicit provenance. Proposed label vocabulary: `activation_complete|partial|expired|unmeasured`; message `sent|queued|delivered|acknowledged|working|review|failed|expired` with separate cancellation/revocation reason; outcome `accepted_merge|accepted_noncode|in_review|blocked|open|cancelled|unverified_done`; cost `reported|estimate|partial_observed|unmeasured`; knowledge `verified_reuse|repeated_discovery|incorrect_reuse|unmeasured`. These are scorecard/UI labels, not permission to invent new underlying event kinds or overwrite historical CLI status.

Current productive-run pairing uses `run_id`, with documented legacy `(agent,run_no)` fallback; no backfilled model turns. Required joins include board ID, unit/ticket ID, runtime/session and provider provenance, message/task nonce/idempotency ID, exact candidate/merge evidence and event time. Where not emitted, T-811 must identify the gap and leave the KPI null. As-of replay and source digest are mandatory for weekly claims.

Independent audit cases: missing ACK despite inbox read; unrelated worker update; delayed message past window; review without verdict; DONE without review; later mutable status; no-token/no-rate cost; partial cost; parent/worker identity collision; retry/idempotency duplicates; open claim cohort; repeated work despite a context link. Each must fail the fabricated inference while preserving eligible/missing counts. These directly exercise the seven existing T-811 FIX defects, not tests that merely mirror presentation.

### Release floor and first improvement cycle

Proposed release floor: every promised control has independent exact behavior evidence; repeatable fresh-board A → B run; authorized separate-client directed work start with zero operator keystrokes for each advertised transport; duplicate/retry/revoke/task-only denial cases pass; nine KPIs have ACCEPTed definitions/provenance/missing rules and a reproducible initial snapshot. A baseline may honestly contain null KPIs; observed native-work, identity/security and the real first-win proof cannot be waived as missing telemetry. No cost-savings or agent-ranking claim until comparable full attribution exists.

Before efficacy claims, fix any disputed T-811 inference and T-831 verdict. Then run a frozen small task-family suite comparing manual agent handoff to Atman-assisted handoff under the same models, acceptance gate and budget. Record all failures, coordination, interventions and elapsed/cost coverage. A dozen matched episodes can validate instrumentation and expose errors; it is not a robust “X% cheaper” launch claim. Use paired outcome data and uncertainty before choosing a routing improvement; the old shadow route agreement is observational, not a counterfactual benefit.

Weekly: one dominant measured error slice → one small change → preserved success/security criteria → matched rerun → before/after with cohort and missing coverage. Useful targets are fewer operator interventions, identity failures, duplicate paid actions and review cycles while preserving completion quality. Publish dollar improvements only when measured. Do not create unrelated optimization work just to grow sample size.

## Date protection and stop conditions

| Date | Concrete endpoint | CEO decision if absent |
|---|---|---|
| Sep 13 | Approve graph cuts and promise; CoS staffs one worker per ready lane; exact corrected identity/transfer integrated; source clarification recorded. | Resolve owner/transport blockers once; do not spin another planner/merge desk. |
| Sep 14 | `atm` source/wheel parity accepted; UI and README working in parallel; actual CoS/CEO/Sol transport matrix captures idle + active results; metric corrections submitted. | If native transport unavailable, choose a genuinely supported supervised/bridge implementation and label it; cannot substitute manual inbox consumption for promised automatic work. |
| Sep 15–16 | Coherent app browser candidate, auth/reconnect + separate-client work proof, independent scorecard replay. | Freeze features; repair only failed promised behaviors. No learned routing, fleet or language-migration expansion. |
| Sep 17 | Independent clean-machine / live acceptance T-812, deployed SHA and rollback T-832, fresh-reader README trial. | If critical behavior fails, state exact blocker and revise release scope/date explicitly. A preview is not the objective. |
| Sep 18 | Public product release with truthful client support, executable entry path and initial measurement snapshot. | CEO release verdict; CoS records deployed revision, residual debt and next measured improvement. |

Steer: the revised T-854 labels → T-815 efficacy → T-855 improvement loop stands. Two cautions: seal and blind predictions; model-assisted independent reviewers do not imply domain/legal review. Repeated optimization against a revealed holdout destroys independence: keep a development slice for fixes and reserve new sealed confirmation for release claims. Scope gates by the named control/family, retain eligible missing predictions and severe-miss budget; “no agreement” is NO_GO, not a favorable relabel. No NER flip, new action or bypass of T-833 thresholds is proposed.
