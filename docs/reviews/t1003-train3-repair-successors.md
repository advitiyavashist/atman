# T-1003 — train-3 repair successor review

Verdict: **ACCEPT all three exact heads**, with the integration order below.

| Ticket | Canonical artifact | Verdict | Finding |
|---|---|---|---|
| T-999 | PR #192 `91ca032db533bc9cd96487e5fc6190d2e491fc17` | ACCEPT | Caller `PATH` remains ahead of fallback directories. `watch --dry-run` skips both identity re-exec and auth gating, while non-dry-run identity fencing remains intact. |
| T-1000 | PR #193 `f8d3c3638df2a2a35a60d0363973ee15353d0c1c` | ACCEPT | Exact `refused` and `refused (...)` receipts remain eligible for the supervised watcher fallback. They are still not native success; `delivered-confirmed`, `held`, `dropped`, and `expired` still suppress the fallback. |
| T-1001 | PR #191 `33872e3a3beb21e275da211dd409892ef039d32b` | ACCEPT | The repair commit changes only the author-home example in a test docstring to `/Users/<operator>/...`; runtime code is unchanged by the repair. |

## Independent checks

- T-999 focused set: 26 passed (`test_watch_once_exit_codes_and_dry_run`, T-875 launch policy, T-954 leader identity, identity precedence). The four initial failures under the restricted sandbox were process-table visibility failures; the same set passed with local process inspection enabled.
- T-1000 focused set: 58 passed (provider wake, T-918 receipt, Claude/Codex transport, reachability, pause/wake, and Codex wake tests). Unix-socket and process-table access were enabled; all review-session identity variables were scrubbed. No provider was called.
- T-1001 focused set: 20 passed (operator-home contract, planted-literal control, and T-959 shared-board tests).
- `git diff --check` is clean for each PR delta.

## Virtual composition

A direct merge of PR #192 onto the failed train `a444bfe` conflicts in `tickets.py` and `tests/test_t954_leader_identity.py`, because PR #192 replaces the old #163/T-954 head already in that train. Do not layer the repair onto `a444bfe`.

Re-cut from `origin/main@e201436` in the established train order, replacing the three failed heads:

1. PR #192 `91ca032` (replaces #163)
2. #162 `38cbe2a`
3. #140 `9b3bc33`
4. PR #193 `f8d3c36` (replaces #146)
5. #133 `2a2a0f1`
6. #164 `43586ee`
7. #165 `9d7731d`
8. PR #191 `33872e3` (replaces #166)
9. #156 `033cb73` (already present through merged history in this recut)

This virtual recut produced local head `c58430054ef6ab3df14909c018ef6ebac2a0e1fd`; all nine named heads are ancestors. The combined focused set passed: **104 passed in 74.17s**. Applying only the three successors in arbitrary order can reintroduce the stale T-956 `cmd_note` provenance behavior carried in old branch history; preserving the established order keeps #162's `whoami(a.by)` correction.

## Duplicate PR #190

PR #190 `30e9d69afc204f6f830bb53702a418b28c2b9275` and PR #193 have the same parent (`2198e3f`), identical trees, and the same stable patch id (`0cb94ddf5e609fd1b16144f9e9709cbb9342fe85`). **PR #193 is canonical** because it is the exact board-pinned artifact from the uniquely assigned T-1000 worker. Close PR #190 as a duplicate after recording the disposition; do not merge both.

No full suite, implementation change, provider call, or merge was performed.
