# T-818 remote control: authenticated command and evidence-only states

Implements the frozen T-830 contract (Steer `docs/product/atman-remote-control-contract.md`,
merged f9d9ee2). An operator on a separate client authenticates with a
board-scoped bearer token, selects a stable role, sees the runtime that role
resolves to now plus the permissions the token grants, sends a bounded task,
and watches delivery states that only advance on board evidence.

Code: `remote_control.py` (policy, records, evidence, router), `tickets remote-control` (alias `tickets rc`;
CLI + server wiring in `tickets.py`), tests in `tests/test_t818_remote_control.py`.

## Where it sits

The live board is the file board (`messages.jsonl`, `agents/*.json`,
`workforce.json`, `aliases.json`, `coordination/state.json`, `trajectories.jsonl`).
Persistent Codex, Cursor and Claude seats are woken through `session_adapters.py`.
Remote control therefore posts through `tickets.post_message` and wakes through
`tickets.wake_recipients`, the same function `tickets msg` calls. There is no
second transport a remote task could use that the local CLI would not.

The scoped sqlite API under `src/ticket_board/server/` is a separate managed
runner system and is not involved.

Records live under `.tickets/remote/` (dir 0700, files 0600):

| File | Holds |
|---|---|
| `state.json` | token hashes + grants, revoked seats, idempotency replay cache |
| `dispatches.json` | one record per dispatch/message attempt (ids, digests, links, terminal state) |
| `audit.jsonl` | append-only receipts: actor, credential class, action, target, refs, digests |

A raw token appears once, in the mint response. It is never stored, logged,
printed by `tokens`, put in a message, or echoed in an error.

## Credentials and permissions

| Credential | Who | How |
|---|---|---|
| operator token `rop_…` | a person on a phone/laptop | `tickets rc mint --operator NAME` on the host; sent as `Authorization: Bearer` |
| seat identity | the addressed agent | `$TICKET_AGENT` on the host running `tickets rc receipt` |
| runner/session | provider sessions | untouched; provider login stays with the provider |

A token starts with `read` + `message` on its board. Everything else is explicit:

| Grant | Flag | Rule |
|---|---|---|
| `dispatch` | `--grant dispatch` | send a bounded task; may be scoped with `--target role` |
| `retry` | `--grant retry` | retry a failed/expired attempt as a linked attempt |
| `cancel`, `reassign`, `revoke` | `--step-up X` | short-lived (15 min default, 1 h max), separate from the token lifetime |
| `review` | `--grant review` | reserved; no remote review route in V1 |
| `merge`, `destructive`, `shell`, `secrets` | none | denied for every remote caller; minting them is refused and audited |

Denials are visible: 403 with the grant named, an audit row, and the served page
shows merge/clear controls disabled with "denied in V1".

Before any action the client must resolve the role and echo the `confirm`
digest, which binds token id, seat, session, provider and the grant set. A
dispatch without it is 409 `confirmation_required` carrying the resolution.

## Role resolution (never a hardcoded id)

`resolve` tries, in order: an exact seat name; `aliases.json` (`ceo`, `cos`);
`coordination/state.json` roles by full id or last segment (`steer.ceo` answers
to `ceo`); `master`/`cos` from `master.json`; a single seat holding the role in
`roles.json`. Several holders is 409 `role_ambiguous` with candidates. An alias
or role that points at an unregistered (retired) seat is 409 `role_unbound`
with the rebind command, never a silent fallback.

The runtime shown comes from `board_snapshot`: provider, lifecycle, wake mode,
reachability and adapter state, redacted session, auth state, usage limit,
active run, held tickets, last wake receipt.

## States are evidence

| State | Evidence required |
|---|---|
| `sent` | request accepted with a stable `rc_…` id |
| `queued` | the message record exists in `messages.jsonl` addressed to the seat |
| `delivered` | the seat's inbox watermark consumed that message id (first observation is persisted) |
| `acknowledged` | a receipt message from that seat referencing the dispatch id (`tickets rc receipt … --ack`, or a plain reply naming the id) |
| `working` | a trajectory `run_start` by the seat after delivery/ack, a claim/update on the dispatch ticket by the seat, or a `--working` receipt |
| `review` | the dispatch ticket is in review under that seat, or a `--submitted` receipt when no ticket is bound |
| `failed` | auth not ready / usage limit / seat revoked at send, a wake failure stamped with the message id, or a `--failed --reason` receipt; always with one recovery action |
| `expired` | the delivery window ended before work; recovery is `tickets rc retry` |
| `canceled` | cancel before delivery, or after delivery only once a child-exit receipt (`run_end` by the seat or `--canceled`) exists |

A wake label (`woken`, `queued-offline`, `watch-poked`), an SSE frame or a pid
never advances a state on its own. `detail` explains a queue: `queued-offline`,
`busy: T-123`, `task-only seat: an ordinary message waits for its next task
trigger`. Task-only seats get a wake only from a `dispatch` (kind `task`);
`message` stays queued for them.

Cancel is three flags on the record: requested, acknowledged (`--cancel-ack`),
stopped. Only stopped is `canceled`. Remote never kills a process; the hard stop
stays a local operator action (`tickets spawn <seat> --stop`).

Retry and reassign create a new attempt with `attempt+1`, the same
`logical_task`, `prior_attempt`, and for reassign `reassigned_from` + reason.
A reassign of an attempt that is `working` or `review` is 409 `active_work`.
Revoking a seat fails its queued attempts (reason `revoked`) and refuses new
delivery until `--lift`.

Every mutation needs an idempotency key (`Idempotency-Key` header or
`idempotency_key` body field). Same key + same input replays the stored
response (`replayed: true`, one message on the board); same key + different
input is 409 `idempotency_conflict`. Keys are scoped per token.

## HTTP API (`tickets rc serve`)

Loopback by default. A non-loopback bind requires `--behind-tls-proxy` because
bearer tokens travel in clear text otherwise; reach the loopback port through
an authenticated tunnel (Tailscale, cloudflared, ssh -L) from the phone.

| Method | Path | Grant |
|---|---|---|
| GET | `/` | the phone page (token kept in page memory only) |
| GET | `/remote/v1/whoami` | read |
| GET | `/remote/v1/roles` | read |
| GET | `/remote/v1/resolve?role=ceo` | read |
| GET | `/remote/v1/dispatches[?limit&seat]`, `/remote/v1/dispatches/{id}` | read |
| GET | `/remote/v1/audit` | read |
| POST | `/remote/v1/message` `{role,text,ticket?,confirm}` | message |
| POST | `/remote/v1/dispatch` `{role,objective,exit_criteria?,ticket?,expires_in?,confirm}` | dispatch |
| POST | `/remote/v1/dispatches/{id}/retry` | retry (+dispatch) |
| POST | `/remote/v1/dispatches/{id}/reassign` `{role,reason,confirm}` | step-up reassign |
| POST | `/remote/v1/dispatches/{id}/cancel` `{reason?}` | step-up cancel |
| POST | `/remote/v1/seats/{seat}/revoke` / `unrevoke` `{reason?}` | step-up revoke |
| POST | `/remote/v1/tokens/self/revoke` | any |
| POST | `/remote/v1/merge`, `/shell`, `/clear`, `/delete`, `/secrets` | 403 `denied_in_v1`, audited |

A body carrying `from`, `sender`, `actor` or `operator` is 403
`sender_identity_rejected`: the sender is the credential. A present `Origin`
must match `Host`. Bodies are JSON, ≤ 64 KiB. Objective/text/reason that look
like a credential are 400 `secret_in_payload`. The server logs nothing.

Example from a separate client:

```sh
export T=rop_…            # from `tickets rc mint --operator advitiya --grant dispatch --step-up cancel`
curl -s -H "Authorization: Bearer $T" "http://127.0.0.1:8766/remote/v1/resolve?role=ceo"
# -> {"resolution":{"seat":"atman-ceo-…","source":"alias"},"runtime":{...},"permissions":{...},"confirm":"…"}
curl -s -H "Authorization: Bearer $T" -H "Content-Type: application/json" -H "Idempotency-Key: $(uuidgen)" \
  -d '{"role":"ceo","objective":"post a one-line status on T-818","confirm":"<confirm>","expires_in":"15m"}' \
  http://127.0.0.1:8766/remote/v1/dispatch
curl -s -H "Authorization: Bearer $T" http://127.0.0.1:8766/remote/v1/dispatches/rc_…
```

## CLI

```
tickets rc mint --operator NAME [--grant dispatch] [--grant retry] [--step-up cancel] [--expires 12h] [--target ceo]
tickets rc tokens | revoke-token rtk_…
tickets rc serve [--port 8766] [--host 127.0.0.1] [--behind-tls-proxy]
tickets rc roles | resolve ROLE
TICKETS_REMOTE_TOKEN=rop_… tickets rc dispatch ROLE --objective "…" [--exit "…"] [--re T-1] [--expires 30m]
TICKETS_REMOTE_TOKEN=rop_… tickets rc message ROLE "text"
tickets rc status rc_… | list | audit
TICKETS_REMOTE_TOKEN=rop_… tickets rc cancel rc_… --reason "…" | retry rc_… | reassign rc_… ROLE --reason "…" | revoke-seat SEAT [--lift]
tickets rc receipt rc_… --ack | --working | --submitted [--artifact …] | --failed --reason "…" | --cancel-ack | --canceled
```

The CLI operator commands authenticate with the same token a phone would
(`$TICKETS_REMOTE_TOKEN` or `--token-file`), so the host shell has no
privileged side path.

## Acceptance runbook (separate client → CEO role → Codex/Claude seat)

Never on the live board while it is coordinating work you did not agree with
the CEO; use a throwaway board or a scheduled window.

1. On the host: `tickets rc mint --operator <you> --grant dispatch --step-up cancel --expires 2h`
   and `tickets rc serve` (loopback). Open a tunnel to port 8766 from the phone.
2. On the phone: open `/`, paste the token, Connect, pick `ceo`, Resolve. The
   page shows the seat the alias resolves to now, provider, reachable, auth,
   and the grants; merge/clear are disabled.
3. Dispatch a bounded objective with a 15 m window. Watch state: `queued`
   with `wake` label from the real transport, then `delivered` when the seat's
   inbox consumes it, `acknowledged` on `tickets rc receipt rc_… --ack`
   from that seat, `working` on its run_start or ticket claim/update.
4. Ask for a failure path: revoke the token from the page (`tokens/self/revoke`)
   and confirm 401; or dispatch to a seat whose auth is `login_required` and
   see `failed` with the login recovery.

Transport labels come from `session_adapters.wake_seat`; the T-857 fixes
(Codex WebSocket handshake, ack-gated Claude inject) change the label truth,
not this flow. `delivered` here never trusts a label.

## Not in V1

Remote review decisions, remote merge, any process kill, TLS termination
inside `tickets rc serve`, and per-agent HTTP tokens for receipts (seats
sign receipts with their local identity on the host).
