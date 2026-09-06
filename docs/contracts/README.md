# Ticket Board V1 — contract pack (T-178)

Frozen interface for epic E-010. Derived from `docs/interface-v1.md` and the
`docs/messages-and-runners.md` scope amendment on `codex-interface-design`.

| Artifact | What it is |
|---|---|
| `openapi.yaml` | OpenAPI 3.1. 31 paths, 34 operations, 96 component schemas. |
| `dependent-notes.md` | Per-ticket handoff for T-179/T-180/T-181/T-183/T-187. |
| `../../tests/fixtures/` | 124 JSON fixtures, one per screen/state/error. |
| `../../tests/fixtures/manifest.json` | Fixture path → component schema name. |
| `../../tests/test_contracts.py` | The acceptance bar. 138 checks. |

## Running the validation

```sh
pip install -e '.[contracts]'
python -m pytest -q
```

The contract deps are an optional extra so a bare checkout can still run the CLI
tests. CI sets `TICKET_BOARD_CONTRACTS_REQUIRED=1`, which turns a missing
toolchain into a failure rather than a skipped test — otherwise the acceptance
bar could "pass" by never running. See `.github/workflows/contracts.yml`.

## What the suite enforces

- Every fixture validates against the component schema the manifest names for it.
- The manifest and the fixture tree agree — no unlisted files, no missing files.
- Every `$ref` resolves; no component schema is orphaned; every schema is valid
  JSON Schema 2020-12; every operation has a unique `operationId`.
- Every screen has a fixture, and every listing screen has an **empty-state**
  fixture.
- The offline, blocked, review, stale-stream and replay-gap states are all
  covered by at least one fixture.
- Every declared `ErrorCode` has a published example, and error statuses match
  their code family.
- No fixture contains anything shaped like a credential; `code`/`token` fields
  carry documented placeholders.

The suite was mutation-checked: a wrong scalar type, a dropped required field, a
stray extra property and a planted secret each fail it.

## Freeze rules

This interface is frozen for dependent implementation. T-179, T-180, T-181,
T-183 and T-187 build against it as published.

To change it after the freeze:

1. Change `openapi.yaml` **and** the affected fixtures in the same commit. The
   suite fails if they drift apart, which is the point.
2. A change that adds an optional field or a new route is additive — note it in
   `dependent-notes.md` and tell the affected lanes on the board.
3. A change that removes or renames anything, or makes an optional field
   required, is breaking. Raise it with the master before merging; a dependent
   lane may already have built against the old shape.
4. Bump `info.version` on any breaking change.

## Contract decisions worth knowing

These were judgment calls, not transcription. Each is documented inline in
`openapi.yaml` at the point it applies.

- **Actor is bound from the credential, never from the body.**
  `interface-v1.md` says every mutation carries "actor, request_id,
  expected_version"; `messages-and-runners.md` says sender identity comes from
  the credential "never from request body fields". These conflict. Resolved in
  favour of the stricter rule: the body carries `request_id` and
  `expected_version` (plus a lease field where required), the actor comes from
  the credential, and an `actor` field in a body is rejected. See
  `MutationEnvelope`.
- **`dependency_blocked` is derived, not a state.** `TicketState` has no
  dependency-blocked member; the flag rides on an otherwise `open` ticket, per
  "blocked is an explicit state; dependency-blocked is derived".
- **Review acceptance pins a SHA.** `ReviewDecisionRequest.evidence_sha` must
  equal the SHA on the submitted review. This is what makes "done requires
  reviewer acceptance of the exact submitted artifact" enforceable rather than
  aspirational; re-resolving a branch name at accept time would not.
- **Tasks are created through `POST /messages/{message_id}/task`, not by setting
  `intent: task` on a send.** Keeps the required outcome/assignee/ticket
  validation in one place.
- **`started` is not spawn.** A run reports `started` only once its runtime
  session exists and, for an execution request, its claim succeeded. Anything
  else is 422. Delivery is not acknowledgement.
- **Heartbeat and progress are separate fields** on both `Agent` and `Ticket`,
  so a live-but-stalled session is visible rather than hidden behind liveness.
- **Routes the design docs implied but did not list** were added so each screen
  can be built: `GET /tickets/{ticket_id}`, `/agents`, `/activity`, `/master`,
  `/members`, plus the review decision, blocked toggle and session-lease
  revocation the acceptance criteria require.

## Honest limits

Nothing here has been implemented, served or measured. Two numbers appear in the
document and both are **targets copied from the design docs**, not observations:
the two-second local dashboard update and the five-second managed-agent start.
They are labelled as targets at each occurrence. No runner exists yet; no
latency, throughput or reliability claim in this pack is evidence of anything.

Server-side behaviour described in `description` fields — atomicity, fencing,
deduplication, ACL filtering — is a specification for T-179/T-180/T-187 to
implement, not a description of code that exists.
