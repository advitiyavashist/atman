# T-976 first-user journeys — bounded diagnostic

Phase: **diagnostic only**. This is **not** final release acceptance.
Agent: `atman-consolidation-verify-cursor-0915` on 2026-09-15.
Supported source pin: `origin/main`
`0102a072d6999db3f0eb1fa863d5b78d7e0fa763`.
Receipt-only worktree tip: `61fabc2` (T-968 markdown; no runtime change).
Author interventions: **0**. Live inference: **none**. Full suite: **not run**.

## Unmerged scope (blocked/not-run, not failed)

| Work | Head | On `0102a07`? |
|---|---|---|
| App T-810 / PR #156 | `033cb73` | no |
| Recovery T-977 | `9ae6cd8` | no |
| Assignment T-979 / PR #174 | `656f983` | no |
| Onboarding docs T-972 / PR #171 | `fbfc76d` | no |
| Independent accept T-944 | absent on this pin | no |

## Journey outcomes

| Journey | Outcome | What ran on this pin |
|---|---|---|
| Normal | **not passed** | CLI init, join (custom stub), objective, dependent create, claim, persisted `artifact.txt` **passed**. Review from a fresh `git init` (`main`) **refused**. App first screen, canonical T-972 guide, T-944 exact-artifact accept: **blocked/not-run**. |
| Interrupted | **partial / not the release journey** | Markdown `handover --file` + `assign` replacement + intact intermediate file + one `agents/*.json` ticket field **passed**. Structured handoff, `owner_generation` lease, app reload, stale-owner accept fence: **blocked/not-run**. |
| Interchangeability | **blocked/not-run** | Two custom stubs can join. That is **not** cross-provider recovery. No live second harness. T-977 same-provider vs cross-provider cases are unmerged. |
| Adverse | **mixed** | Missing `atm` on a clean PATH: honest `rc=127`. Review-on-`main`: honest refusal naming worktree/`--force`. `--force` review **passed**. Second active assign **failed** (main assigned T-001 and T-002 to the same worker). Missing auth, stale upgrade, app reconnect: **blocked/not-run**. |

## Recoverable next steps observed

1. `tickets review` on a default `main` primary worktree prints `RULE: submit from your own worktree branch, not 'main' (or --force)` and exits 1. `--force` submitted (`T-001 -> IN REVIEW`). A non-main branch still refused uncommitted files (`commit before submitting … or --force`).
2. `join`/`next` on `main` also print the worktree-add recipe. A first user who only follows `git init` + CLI create/claim cannot finish review without that extra git step.
3. A clean PATH without `atm` does not fabricate a command.

## Defect still on this pin

Second active hold: `assign T-002 --owner worker` while worker already held T-001 succeeded (`T-002: claimed for worker`). Matches the T-979 / PR #174 reason for existing. Not fixed on `0102a07`.

## Supporting existing tests (bounded)

- `tests/test_byoa.py::test_join_records_harness_and_cmd` **passed**
- `tests/test_byoa.py::test_join_custom_without_cmd_is_refused` **passed**
- `tests/test_t437_clear_prev_owner_ticket.py::test_assign_clears_previous_owner_ticket_and_watch_omits` **failed** (`alice["ticket"]` empty after `next`, before assign). Not treated as a new product claim.

## What this does not certify

Stub boards do not certify Claude, Codex, Cursor, or Agy. Branch tests of PR #156 / T-977 / PR #174 / PR #171 do not certify this main pin. T-976 final acceptance stays repinned to the integrated app + canonical docs + source install + ownership/recovery/review candidate.
