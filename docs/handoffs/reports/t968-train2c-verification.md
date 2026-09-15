# T-968 merge-train 2c verification

Verdict: **ACCEPT** for `ceo/merge-train-2c-0914` at
`e7a8b4961ad64dfe49f8e0360697ab6c9e7a4848` against sealed baseline
`a1a95db211eee662ceea0acad90105eb62a032b5`. Independent classification by
`atman-consolidation-verify-cursor-0915` on 2026-09-15. No implementation
changes, no full-suite rerun, no baseline suite rerun, no merge.

## Suite receipt (completed run; not repeated)

Readonly compare of the completed candidate log against the sealed T-958
`suite-main.log` (16/16 custody checksums OK):

| Origin | Totals |
|---|---|
| Sealed baseline `a1a95db` | 11 failed, 2213 passed, 24 skipped, 11 xfailed |
| Candidate `e7a8b49` | 10 failed, 2299 passed, 24 skipped, 11 xfailed, 2637s |

`FAILED(e7a8b49)` minus the 11 sealed baseline IDs is one node:

`tests/test_t683_session_adapters.py::test_wake_delivery_is_deduped_by_message_id`

Suite assertion: `wake_seat(...)` returned `woken` instead of
`delivered-unconfirmed`. Two sealed baseline IDs are absent on the candidate
(`test_review_tasks_cos_once_then_inbox_clears_repeat_wake`,
`test_claim_update_review_done_are_all_recorded`).

## Isolated reproduction (this ticket)

Same hermetic recipe as the recorded preflight origins (`env -i`, Python 3.9.6,
pytest 8.4.2, `PYTHONNOUSERSITE=1`, sanitized HOME/TMP). Candidate used the
existing `sha-e7a8b4961ad6` origin. Baseline origin was rebuilt with
`scripts/hermetic_preflight.py --sha a1a95db` (preflight PASS) because the
T-958 workdir was gone; that rebuild is origin-only, not a suite.

| Origin | Isolated runs | Pass | Fail | Fail assertion |
|---|---|---|---|---|
| `e7a8b49` | 33 | 30 | 3 | `woken` == expected `delivered-unconfirmed` |
| `a1a95db` | 30 | 29 | 1 | same |

The candidate-only suite row is therefore a **pre-existing flake**, not a
deterministic train regression. T-958 flake discipline: do not call a new
failure a regression unless isolated reruns on both SHAs show candidate-fail /
baseline-pass.

## Attribution

Claude inject and this test are byte-identical on both SHAs:

- `CLAUDE_ACK_WAIT_SECS = 0.0`
- `_claude_ack_ok`, `_recv_json_line`, `_poke_claude_wake`, `_poke_claude_until`
- `FakeInbox` and `test_wake_delivery_is_deduped_by_message_id`

`_recv_json_line` always makes one read, so a FakeInbox ACK that is already in
the socket buffer is treated as `woken`. That race exists at the sealed
baseline. It is **not uniquely caused** by any of the nine train PRs.

Train PRs that touch `session_adapters.py` at all:

- PR **#145** `6aeb508` (T-861 Cursor/Agy wake)
- PR **#152** `6015b0f` (T-947 Codex wake)

Neither changes the Claude poke path or this test body. The other seven PR
heads do not touch `session_adapters.py` or this test. No FIX attribution.

## Train composition (unchanged)

`e7a8b49` = `a1a95db` + #147 `eb5e2b6`, #153 `679412a`, #152 `6015b0f`,
#159 `3fa65c0`, #160 `67db67c`, #157 `3f2b0ea`, #158 `0fba20d`, #161 `23c7cd6`,
#145 `6aeb508`. Dropped culprits #162 `715939f` and #140 `05bd390` remain
non-ancestors.

## Bounds

- Author interventions: 0
- Tested SHA (immutable candidate): `e7a8b4961ad64dfe49f8e0360697ab6c9e7a4848`
- Worktree at start: `0102a072d6999db3f0eb1fa863d5b78d7e0fa763` (this receipt only)
- Private logs stay in operator custody (`t958/`, `t968/`); not committed
