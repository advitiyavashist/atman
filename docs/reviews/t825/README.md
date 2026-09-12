# T-825 independent review of T-804

Verdict: **FIX** for PR #101 / `atman-identity-cursor-0912@97d4b1c`, initially
reviewed against `origin/main@6cae38f` on 2026-09-13 SGT. During the review,
the same T-804 patch landed on `origin/main` as `501023d`; only unrelated
metrics code differs between the identity implementation in `97d4b1c` and
`501023d`. The failures below reproduce unchanged on current
`origin/main@459edc7`. The provider-reuse guard and ordinary history retention
work, but the identity transition is not atomic or complete enough to trust
for a persistent CEO seat.

## Reproduction

The candidate was checked out detached at `/private/tmp/atman-t825-candidate`.

```sh
python3 -m pytest -q \
  tests/test_t804_identity_isolation.py \
  tests/test_byoa.py \
  tests/test_t409_watch_teardown.py \
  tests/test_t683_session_adapters.py \
  -p no:cacheprovider \
  --basetemp=/private/tmp/t825-candidate-focused-unsandboxed
# 103 passed in 143.12s

ATMAN_CANDIDATE_ROOT=/private/tmp/atman-t825-candidate \
python3 -m pytest -q \
  docs/reviews/t825/test_t825_adversarial.py \
  -p no:cacheprovider \
  --basetemp=/private/tmp/t825-review-pytest-final
# 11 failed, 5 passed in 27.72s

ATMAN_CANDIDATE_ROOT="$PWD" \
python3 -m pytest -q \
  docs/reviews/t825/test_t825_adversarial.py \
  -p no:cacheprovider \
  --basetemp=/private/tmp/t825-current-main-review
# current origin/main integration: 11 failed, 5 passed in 27.53s
```

The focused session suite needs normal host process and Unix-socket access;
its first sandboxed run had 13 permission failures, then passed 103/103 when
rerun with those capabilities.

## Findings

1. **A failed spawn transfer destroys the valid old identity before the new
   provider is authenticated.** `cmd_spawn` clears auth, runner, limit and
   endpoint at `tickets.py:11601-11607`, then performs the target-provider auth
   preflight at `tickets.py:11625-11629`. With a Cursor seat and unavailable
   Codex login, `spawn --harness codex --transfer` exits nonzero while
   `workforce.json` still says Cursor and the agent record now contains Codex
   `login_required`; the previous Cursor auth, runner context and limit are
   gone. Transfer must prepare and validate first, then commit all identity
   state once. A failed prepare must preserve the old seat byte-for-byte.

2. **Role-alias handover is neither audited nor exclusive in workforce
   metadata.** `_guard_seat_identity` emits an audit only when the runnable
   owner itself changes provider (`tickets.py:8143-8153` and packaged
   `cli.py:3939-3949`). Moving `ceo` from `atman-ceo-cursor` to the new unique
   `atman-ceo-codex` therefore emits no transfer record. `_bind_role_alias`
   updates only `aliases.json`; the old workforce row keeps
   `role_alias=ceo`, so two rows claim the stable role. The audit must name the
   alias, old holder, new holder and actor, and the old row must lose the role.

3. **The packaged CLI does not clear a native provider endpoint.** Its
   `_strip_identity_bound_state` imports top-level `session_adapters` and
   suppresses every exception (`src/ticket_board/cli.py:3920-3924`). Executed
   as the shipped source CLI, that module is not on its import path, so a
   Cursor endpoint containing the prior token survives a successful Codex
   transfer. Root `tickets.py` passes the same test. Both shipped copies must
   call the same endpoint-removal implementation and prove parity.

4. **Remote adapter credentials remain live after provider transfer.** Root
   `_strip_identity_bound_state` removes only the native endpoint
   (`tickets.py:8120-8127`). A live `remote register` lease remains `online`
   after `join remote-ceo --harness codex --transfer`; status continues to
   expose the old bridge until TTL expiry. Transfer must revoke/fence remote
   adapter state immediately, including any pending claim, before reporting
   that session and endpoint state were cleared.

5. **Concurrent same-provider rejoin crashes both CLIs.** Forty-eight
   simultaneous idempotent Cursor rejoins of one existing seat produced
   `FileNotFoundError` from `os.replace` in root and packaged copies. Join and
   alias updates use shared fixed names such as `roles.json.tmp`,
   `workforce.json.tmp`, `aliases.json.tmp` and `<agent>.json.tmp`; one process
   replaces another process's temporary file. The whole identity decision and
   multi-file commit also lacks a seat/board lock, so avoiding the exception
   alone would still permit stale conflict checks and lost updates.

6. **A live CEO alias transfer stays suppressed by an old manual limit.** At
   00:13 SGT, the live board ran
   `join atman-ceo-codex-0912 --alias ceo --transfer --harness codex
   --persistent`. The new supervised Codex endpoint registered and
   `tickets harness auth atman-ceo-codex-0912 --harness codex` returned
   `Ready`, but `tickets who` still rendered `limited !` from a 44-minute-old
   Claude-fable manual-limit note. There is no transfer audit at that time.
   This follows the code: state cleanup is conditional on `prev and transfer`
   (`tickets.py:8143`), so an alias-only transfer onto an already-Codex seat
   does not inspect or clear stale identity state. `pending_work` then returns
   immediately on any manual `limit` (`tickets.py:8467-8469`), preventing the
   otherwise-ready persistent CEO from receiving work. Manual limit records
   need provider/session/runner provenance and transfer must invalidate a
   mismatched record after successful target authentication. The adversarial
   suite reproduces this for both CLIs.

## Behaviors that passed

- Cursor-to-Codex reuse without `--transfer` is refused in both CLIs.
- A same-provider sequential rejoin preserves auth, limit and message history.
- Transfer is refused while the seat owns a claimed ticket in both CLIs.
- Root transfer clears its native endpoint.
- The candidate's authored 8 identity tests and 95 adjacent BYOA, teardown and
  session-adapter tests pass.

## Bounded acceptance contract

Repair is tracked as T-828, inserted upstream of T-818 and T-812.

Centralize join/spawn transfer in one implementation used by root and packaged
CLIs. Serialize the identity check plus commit with a seat/board lock and
unique atomic temp files. Validate target provider/auth before mutation; on
commit, clear native and remote endpoint credentials, provider-scoped auth,
runner, limit, failure and command state; give limit records explicit
provider/session/runner provenance; update provider and alias ownership;
remove stale alias metadata; and append one audit record. Preserve ticket
history, messages, roles and same-provider session state. The adversarial file
must pass for both copies, alongside the 103-test focused suite above.
