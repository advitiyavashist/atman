# T-687 Team/Connect auth readiness

Build the connect/reconnect surface on the T-685/T-686 contracts. T-830:
provider secrets never enter the Atman UI; login stays on the enrolled host.

## Behavior

- Team cards show provider, enrolled runner (host/user/kind/origin), state,
  checked time, `identity_label`, and one recovery action.
- `Ready` is only shown when the stored blob is authoritative on the enrolled
  runner. Reachable/offline is a separate tag.
- `paused-auth` is distinct from usage quota and from offline.
- Recheck on the enrolled host runs `tickets harness auth` (zero-model status).
  Off-host and sandbox UIs show a copyable command and do not execute.
- POST `/auth-reconnect` rejects secret-shaped fields and `login: true`.
  `ran` is always false: queued work may become eligible, never claimed started.

Tests: `tests/test_t687_auth_connect.py` plus T-685/T-686 contract suites.
T-688 owns live provider smoke.

Browser: throwaway board (never the live Steer board) at `tickets ui #agents`.
Team lede and seats: `docs/t687-team-auth-login-quota-1440.png`.
Card payload (`ready=false`, paused-auth vs quota, copyable recovery, no secret fields)
was confirmed from `/board.json` on that same UI.

