# T-686 auth V2 probes, spawn gates, pause/resume

Implements the frozen T-685 contract in `tickets.py`. Does not start models.
T-688 owns live provider smoke.

## Behavior

- Zero-model status checks (`agent status`, `claude auth status`, `codex login
  status`, or an adapter-declared `auth_status_cmd`) run in the probing
  process and are merged with `merge_auth_check`.
- Join records `runner_context` (host vs sandbox). A sandbox coordinator
  cannot replace an authoritative host blob (`ready`, `quota`, `login_required`,
  `expired`, `network`, …).
- Spawn gates every built-in harness (`claude`, `codex`, `cursor`,
  `cursor+claude`). Custom/BYOA without a declared check is still allowed.
  `--exec` skips the provider CLI gate. Live native sessions skip before
  the gate so skip-on-live does not require a logged-in CLI.
- `dirname(board)` is not git identity. When `workforce.expected_origin` or
  `ATMAN_EXPECTED_ORIGIN` is set, `git worktree add` uses a root whose origin
  matches. Boards with no origin keep local throwaway identity (`local/<sha>`).
- Persistent no-spend states pause with `retry_model=false` and one `alert_id`.
  Matching ready clears pause/alert and resumes a durable wake once.
- Profile metadata is `prf_` tokens under `$TICKETS_CACHE_DIR` or
  `~/.cache/atman/credentials/<board-hash>/` (0600). No secrets on the board.

Tests: `tests/test_t686_auth_v2_probes.py`, `tests/test_t610_auth_recovery.py`,
`tests/test_t685_auth_v2_contract.py`, `tests/test_byoa.py`,
`tests/test_t409_watch_teardown.py` (built-in `--tool` spawn uses a PATH stub
for `codex login status`, not a live CLI).
