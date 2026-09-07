# Organic post-FLAG shadow-vs-actual scorecard

**Generated:** 2026-09-07T23:35:49Z

**FLAG recut sha:** `db6229d` (T-425 bound-write FLAG; live CLI `52f8eaf`)

**Reader:** composer (did not author T-415/T-416 scorecard instrument)

Observational evidence only — not a counterfactual claim about what would have
happened if the shadow pick had been assigned. Pre-FLAG rows are excluded below;
only `post-FLAG` era tickets (bound `run_start` sha ≥ `db6229d` or `705dd05`, or
`at` ≥ 2026-09-07T20:37:13Z) are counted.

## Aggregate (post-FLAG only)

| metric | value |
|--------|-------|
| n (done, bound run_start) | 26 |
| agreement | 1/26 (3.8%) |
| median turns (agree) | 2 (n=1, T-463) |
| median turns (disagree) | 2 (n=25) |
| cost_usd measured | 0/26 (UNMEASURED) |
| cost_usd null | 26/26 |

Turns sourced from `tickets turns --json` (T-425 FLAG inherited; not
reimplemented). Cost axis: every post-FLAG row reports `cost_usd: null`.

## Per-ticket (post-FLAG)

```
ticket   role     actual         shadow         agree turns
T-391    research cursor-modal   -              no        1
T-430    backend  composer       claude         no        4
T-432    backend  composer       claude         no        4
T-439    backend  grok-worker    claude-fable   no        6
T-446    verifica grok-worker    opus-verify    no        2
T-441    verifica cursor-demo    opus-verify    no        1
T-440    infra    cursor-modal   ceo            no        3
T-448    verifica composer       opus-verify    no        3
T-449    verifica grok-worker    cursor         no        2
T-454    verifica composer       opus-verify    no        2
T-455    verifica composer       cursor         no        2
T-456    verifica grok-worker    opus-verify    no        3
T-457    verifica cursor-demo    opus-verify    no        1
T-458    research composer       -              no        3
T-461    backend  grok-worker    claude-fable   no        3
T-462    verifica cursor-modal   opus-verify    no        1
T-389    backend  composer       sonnet-tickets no        6
T-463    console  composer       composer       yes       2
T-465    infra    cursor-demo    cursor-2       no        1
T-466    verifica cursor-demo    opus-verify    no        1
T-467    verifica composer       opus-verify    no        2
T-468    verifica grok-worker    cursor         no        2
T-417    infra    cursor-demo    cursor-2       no        1
T-469    verifica composer       cursor         no        5
T-472    infra    cursor-demo    cursor-2       no        1
T-442    infra    cursor-modal   cursor-2       no        1
```

Full scorecard (all eras) from `tickets route --shadow --score` against live
board `TICKETS_DIR=/Users/kavana/Downloads/steer/.tickets` at generation time.

## Rule 8 — one disagreeing ticket (T-430)

T-430 (`backend`, actual `composer`, shadow `claude`, 4 turns, disagree): the
shadow pick used historical prior for `claude` on backend tickets; `composer`
completed in 4 bound-write turns. Comparable finished backend tickets for
`claude` at claim time had insufficient n for a median (`-` in scorecard).
Whether `claude` would plausibly have finished in fewer than 4 turns is **not
decidable** from the trajectory alone — no `claude` run on T-430 exists. The
disagreement is routing-prior mismatch, not an observed turns delta.

## Recommendation

**INSUFFICIENT** — n=26 post-FLAG done tickets are scored, but cost_usd is
UNMEASURED on all 26 rows (0 measured), so the V1 objective “fewest turns at
least cost” cannot be evaluated on organic data yet. Agreement is 1/26 (3.8%);
median turns are identical for agree vs disagree (2 vs 2), offering no turns
savings signal. Do not start V1 learned routing on this evidence alone; T-470
owns the n≥20 GO/NO-GO close once cost rows exist.
