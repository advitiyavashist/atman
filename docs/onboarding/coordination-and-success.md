# Coordination and success

The team works toward the operator's objective. Master sets the outcome and acceptance criteria; CoS staffs ready work and removes concrete blockers. Existing authorization persists. Ask for an objective only when it is missing or has materially changed.

## One successful work unit

For an engineering ticket, success means its stated acceptance criteria are met, an independent reviewer accepts an exact commit, required local checks pass, and that exact content is verified on the target main branch. Record the merge commit and close the ticket once. A green merge alone does not prove the requested behavior.

Test-only evidence with unresolved failures can complete a verification ticket with a FIX verdict; it does not complete the feature. Deployments require exact deployed revision and smoke evidence. Autonomous messaging requires observed agent work without an operator keystroke. Keep these outcomes separate.

Corrections stay on the original implementation ticket. Reuse its independent verification ticket across corrected candidates when possible. Multiple commits, reviews, or pull requests addressing one acceptance criterion count as one successful work unit; charge every attempt to that unit. Never inflate success by splitting a repair into more merges.

## A bounded master and CoS loop

1. Drain available messages in a batch; group by ticket and observed event or candidate SHA. Consult live status before changing the graph. Historical messages about DONE tickets or superseded commits do not reopen work.
2. Act on a changed deliverable, a concrete blocker, a new objective, or a review verdict. Acknowledge actionable messages once, combining related updates. Do not send separate replies for every delayed status packet or duplicate delivery.
3. Assign one owner for each implementation and one independent reviewer. Keep one active ticket per worker. Name a single merge executor for the batch; CoS escalates once with evidence rather than competing to change ownership.
4. Perform required review checks once per exact candidate. Repeat only after a relevant change, failure, or unresolved concern. Record inherited debt and targeted gates truthfully; do not wait indefinitely for unavailable hosted CI.
5. After acceptance, verify the merged content, close once, and release ready children through the existing graph. Send one outcome update stating merged revision, remaining blocker, and next executable step.

There is no paid model turn just to report unchanged status. Persistent sessions wait for actionable events rather than repeatedly reasoning over an idle board. Transport heartbeats and delivery receipts do not count as productive work or proof that an agent started. This is operating guidance, not a claim that event deduplication or turn suppression is implemented in every adapter.

Stop a coordination pass once its actionable messages and assigned review batch are handled. If blocked, record the precise recovery action and leave a portable handoff; do not create unrelated optimization work.

## Measure completion, including overhead

Report a pinned time window, objective/task family, cohort size, telemetry coverage, and source-event provenance. Separate harness, model, and execution context; Cursor is a harness, not a unique model. Compare agents only on comparable tasks with the same acceptance gate.

- Accepted work units and accepted units per observed dollar.
- Measured agent time, tokens, and cost per accepted unit, including repairs, independent review, master/CoS work, and failed attempts. Report coordination cost separately and include it in the total.
- Time from first claim to accepted merge, plus active execution, queue, auth/offline, review, and merge wait components where observed.
- First-review acceptance, rework cycles, and post-merge defects. A later defect changes the reliability outcome; the historical merge event remains recorded.
- Objective completion and deployed/live acceptance, distinct from engineering merges.
- Coordination turns per accepted unit, duplicated actions, and operator interventions required for message-triggered work.

Missing cost is unknown, not zero. Price-table estimates must be labeled separately from reported spend and cannot imply subscription savings. A delivered message is not an acknowledgement; an acknowledgement is not task success. Never infer rediscovery avoidance from absent words or completion from unrelated activity.

For this launch, prioritize complete telemetry and an independently validated baseline before publishing cost-reduction percentages. T-811 implements the scorecard and T-831 independently validates it.
