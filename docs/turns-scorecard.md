# Shadow-vs-actual turns scorecard

**Generated:** 2026-09-07T21:24:04Z

Observational evidence only — not a counterfactual claim about what would
have happened if the shadow pick had been assigned.

shadow-vs-actual scorecard (observational; not a counterfactual)
agreement pre-T-425 (idle review wakes counted): 0.07 (1/14)
agreement post-FLAG: 0.00 (0/2)
era counts: pre-T-425 (idle review wakes counted)=14  post-FLAG=2 (not mixed into one pct)
ticket   role     actual         shadow         agree turns   actual_med   shadow_med   src flag     
T-318    research cursor-modal   -              no        1            -            - prior pre-T-425
T-384    research cursor-modal   -              no        1            -            - prior pre-T-425
T-391    research cursor-modal   -              no        1            -            - prior post-FLAG
T-388    infra    cursor-modal   ceo            no        2            -            - prior pre-T-425
T-281    verifica sonnet-deploy  opus-authz     no        3            -            - prior pre-T-425
T-396    backend  opus-liveness  opus-liveness  yes       2            -            - prior pre-T-425
T-401    backend  composer       claude-fable   no        8            -            - prior pre-T-425
T-409    infra    grok-worker    infra-2        no        5            -            - prior pre-T-425
T-410    infra    infra-2        ceo            no        4            -            - prior pre-T-425
T-408    infra    sonnet-deploy  infra-2        no       11            -            - prior pre-T-425
T-266    backend  infra-2        claude         no        5            -            - prior pre-T-425
T-419    backend  opus-backend   claude-fable   no        4            -            - prior pre-T-425
T-425    backend  grok-worker    claude         no        4            -            - prior pre-T-425
T-431    verifica composer       opus-verify    no        5            -            - prior pre-T-425
T-430    backend  composer       claude         no        4            -            - prior post-FLAG
T-403    infra    cursor-modal   infra-2        no        2            -            - prior pre-T-425

## Limits

Survivorship: only tickets that reached `done` with a bound `run_start`
(post T-352/T-388 recut) enter the table. Agreement with *n*<2 prints
`n/a` and no percentage. Rows are labelled pre-T-425 (idle review wakes counted) vs post-FLAG from the
bound `run_start` sha against live pin db6229d (else `at` vs 2026-09-07T20:37:13Z); mixed
eras are never one silent pct. Small *n* on disagreement medians is
`-` when either side has fewer than 3 comparable finished tickets.
Turns come from the same `_measured_turns` / `tickets turns --json`
source as T-416 (T-425 idle FLAG is not reimplemented here). No cost
axis until T-403 lands.
