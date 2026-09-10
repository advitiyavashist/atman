# T-685 auth V2 contract

Provider-neutral execution context and credential-profile contract. This
ticket freezes schema, merge rules, CLI/UI fields, and acceptance gates.
**T-686 implements probes, spawn gates, and pause/resume.** Do not start
models here.

Observed failures this contract exists to prevent:

1. **False `login_required`.** T-610 `harness_auth_probe` runs `agent status`
   in the coordinator process environment (`os.environ` of whoever invoked
   `tickets spawn` / `tickets harness auth`). A sandboxed or credential-less
   coordinator then writes `auth_check.state=login_required` onto a seat
   whose **runner host** is already logged in. T-686: a non-matching probe
   must never replace **any** authoritative runner blob (`ready`, `quota`,
   `login_required`, `expired`, `network`, `unavailable`, `unsupported`).
2. **Wrong-repository spawn (2026-09-10).** `tickets spawn` used
   `root = dirname(board)` while `--worktree` pointed at
   `/Users/kavana/Downloads/atman/.worktrees/atman-auth-v2`. The path looked
   like Atman; the git object database was Steer. Repo identity is origin URL,
   not directory spelling and not git HEAD. A shared board in Steer driving an
   Atman worktree is legal.

Code: `auth_v2_contract.py`. Tests: `tests/test_t685_auth_v2_contract.py`.

## Authoritative probe context

A probe is authoritative only when its `execution_context` matches the
enrolled runner:

| Field | Meaning |
|---|---|
| `runner_id` | Stable runner / lease id (T-683), not the CLI pid of `tickets` |
| `runner_kind` | `host` \| `sandbox` \| `container` |
| `hostname` | Kernel hostname of the probe |
| `username` | Account the binary will run as |
| `binary` / `argv0` | Exact status binary on `PATH` used for execution |
| `env_fingerprint` | Hash of credential-relevant env **names** (not values) |
| `worktree` | Agent worktree absolute path |
| `repo_root` | `git rev-parse --show-toplevel` of that worktree |
| `origin_url` | `git remote get-url origin` (stored normalized) |
| `expected_origin` | Ticket/project repo, e.g. `advitiyavashist/atman` |
| `head` | Observational commit of the worktree. **Not** part of auth identity. |

`contexts_match` compares `AUTH_CONTEXT_IDENTITY_FIELDS`: `runner_id`,
`runner_kind`, `hostname`, `username`, `binary`, `argv0`, `env_fingerprint`,
`repo_root`, normalized origin, and enrolled `agent_id`. **`head` is
excluded** so ordinary git commits and checkouts cannot become
`runner_mismatch` or demote an authoritative blob. A different OS user or
credential-relevant env fingerprint is a mismatch and cannot overwrite the
enrolled runner blob.
Spawn still uses origin (`expected_origin`); a ticket that pins a SHA checks
that SHA in T-686 spawn, not in auth merge. Missing `execution_context` on a
legacy T-610 blob means **not authoritative**.

Seat identity is first-class: `execution_context.agent_id` (enrolled seat)
must equal process `TICKET_AGENT` (`ticket_agent`). Live 2026-09-10: the
Atman worktree was origin/main@920644c, but the Cursor child claimed T-685 as
generic `cursor` while the seat was `atman-auth-v2`. That is
`seat_mismatch`, not `login_required`. `preflight_failures` is fail-closed
(`unavailable`) on `seat_mismatch`, `repo_mismatch`, or `runner_mismatch`.

## Credential profiles

`credential_profile_ref` is an opaque `prf_…` token. Metadata lives outside
Git under `~/.cache/atman/credentials/<board-hash>/` (dir `0700`, file `0600`),
same privacy class as T-683 session endpoints. **No secret material** in the
profile file, the board, logs, or UI: no passwords, tokens, API keys,
cookies, or `Authorization` headers. The profile records provider, kind, and
a safe `identity_label` (for example `cursor-cli` or `dev@example.test` only
when the provider status command already prints it). Secrets stay in the
provider CLI or OS keychain.

| Provider | `profile_kind` |
|---|---|
| Cursor | `browser` \| `api_key` \| `auth_token` |
| Claude | `subscription` \| `api_key` |
| Codex | `chatgpt` \| `api_key` |
| remote / custom | `adapter` (adapter-declared zero-model check) |

Adapters must declare a check that does not start a model. If they cannot,
state is `unsupported`, not `login_required`.

## States

`ready | login_required | expired | quota | network | unavailable | unsupported`

T-610 today emits `ready | login_required | quota | unavailable | unsupported`.
T-686 splits `expired` (credential was valid, now rejected) from
`login_required` (never logged in) and `network` from `unavailable` (binary
missing / timeout without a transport error). Never classify auth as quota.

## Merge and pause

`merge_auth_check(previous, incoming, runner_ctx)`:

- Incoming probe with a non-matching context cannot replace **any**
  authoritative runner blob (not only `ready`). Sandbox `ready` must not
  clobber host `quota` / `login_required` / `expired`, **including when the
  merge caller passes `runner_ctx=sandbox`**. Freeze compares
  `previous.execution_context` to `incoming.execution_context` plus
  incoming authority for that stored lineage — not only both blobs against
  the caller `runner_ctx`.
- Silent runner rebind is forbidden in this merge. Changing the enrolled
  runner is an explicit fenced T-686 operation, not an auth-check side
  effect.
- Matching-context probes replace the stored blob.
- Matching authoritative `quota|login_required|expired|…` → `ready` must
  clear `pause.paused` and `alert_id` so a recovered persistent seat resumes
  once.
- Persistent seats (`lifecycle=persistent`, T-683): on every no-spend
  state (`login_required|expired|quota|network|unavailable|unsupported`),
  `retry_model=false`, retain queued work, dedupe **one** operator alert via
  `alert_id=auth:<agent_id>:<state>:<profile_ref>`, resume **once** after
  `ready`. `pause.operator_path` is `login` (login/expired), `quota`,
  `network`, `unavailable` (binary/identity), or `unsupported` (adapter must
  declare a zero-model check — never treat as login, never retry a model).
  Ephemeral seats still do not spend a model turn on failed preflight.

## Schema / CLI / UI migration from `auth_check`

Keep the agent-record key `auth_check`. Additive fields:

| Field | CLI (`harness auth`, `ui --json`) | Dashboard |
|---|---|---|
| `state` | existing | existing Login required / Usage quota; add Expired, Network, Unavailable, Unsupported as distinct tags |
| `detail` | existing `auth_detail` | tooltip |
| `login_cmd` | existing `auth_login_cmd` | exact recovery, never a secret form |
| `identity_label` | replace display of `identity` | safe label only |
| `credential_profile_ref` | printed as `prf_…` | not a connect-secret |
| `profile_kind` | new | next to provider |
| `authoritative` | new | hide non-authoritative warnings from overwriting Ready |
| `execution_context` | `context:` one-line host/binary/origin | T-687 Connect surface |
| `pause` / `alert_id` | new | paused-auth ≠ quota/offline |

Legacy blobs without context remain readable; they are non-authoritative.
T-610 Cursor-only spawn gate stays until T-686 gates every built-in spawn.
`tickets.py` is unchanged in T-685.

CLI flags for T-686 (specified, not implemented): probe must run on the
enrolled runner host; a coordinator may only record a **non-authoritative**
observation. Reconnect (T-687) executes on that host or prints copyable local
instructions; the Atman UI never collects provider secrets.

## Repo identity requirement

`expected_origin` for this epic is `advitiyavashist/atman` at base `920644c`
when the ticket says so. `spawn_repo_identity_ok`:

- `git worktree add` root origin must match `expected_origin`.
- If the worktree already exists, its origin must match too.
- `dirname(board)` is not the repo identity (Steer board + Atman worktree).
- `TICKET_AGENT` must equal the enrolled seat name. A generic `cursor` child
  on an `atman-auth-v2` seat is a failed preflight. `validate_auth_check`
  requires `execution_context.ticket_agent == execution_context.agent_id`.

## Acceptance gates (deterministic)

`tests/test_t685_auth_v2_contract.py` must pass. T-610
`tests/test_t610_auth_recovery.py` must still pass. T-688 owns live provider
smoke; this ticket does not call paid CLIs.

Out of scope: implementing `harness_auth_probe` on the runner, pause wiring
in `cmd_watch`, Connect UI, live login.
