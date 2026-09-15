# T-1009 onboarding runtime review

Base: `origin/main` at `e201436ea72b21601c94811347ed90ea19cae705`.

## Verdicts

- **ACCEPT PR #197** at exact head `921228411b8d1c46b0953b501117baf9e88960f7`.
  Fresh quickstart, worker join, and worker next consistently teach `atm` and
  end the worker loop at `atm review` with an exact artifact. The coordinator
  queue keeps `atm accept`, `atm merge`, and `atm done` as distinct ordered
  steps. The `tickets` console name remains a compatibility alias.
- **ACCEPT PR #196** at exact head `d2944c84708f37cd100c559a75156cb3fcc07f63`.
  Harness availability and usage output contain generic capability, auth,
  usage, and next-action guidance only. Board-specific model, quota, and
  staffing policy remains in that board's brief and reaches only its worker
  prompt.

## Evidence

PR #197 exact-head focused selection: **50 passed** (`test_t1004_first_run_output`,
`test_t322_quickstart`, `test_t788_ceo_connect`, and
`test_t979_assign_one_active`). PR #196 exact-head catalog, quota, onboarding,
template, and prompt-inheritance selection: **37 passed**.

`git merge-tree --write-tree` composed the sibling heads without conflicts as
tree `47cd9df9f61096046f0da2ef2083b6a7dcb62a25`. A temporary two-parent commit
over that tree passed the combined focused selection: **87 passed**.

Two disposable Git repositories used separate boards, homes, session IDs, and
policies (`POLICY-ALPHA-BAN-MODEL-ORION` and
`POLICY-BETA-QUOTA-HOLD-UNTIL-RESET`). Board A ran through `atm`; Board B ran
through the `tickets` alias. Both quickstarts and next outputs ended at
`atm review`; Board A's review queue printed accept, merge, then done. Both
catalogs omitted both policy tokens and the former hard-coded Claude, Gemini,
and Codex policy phrases. Each prompt contained its own token and excluded the
other board's token. Harness discovery ran with an empty binary directory, so
no provider executable was available or called.

One broader PR #196 trial included
`test_master_template_and_howto_start_with_onboarding`, which fails because
the unchanged `docs/onboarding/master-howto.md` puts the accepted-merge link
before the onboarding sentence. The same isolated test fails at the exact
base `e201436`; the PR has no diff for that file. It is therefore baseline
test drift, outside either repair and outside these ACCEPT verdicts.

No implementation, provider call, full suite, merge, or live-board mutation
was performed beyond the required T-1009 status/report operations.
