# CLI ↔ contract mapping (T-211)

Written 2026-09-07 for T-180/T-182/T-184, which will build against
`openapi.yaml` while `tickets.py` is what actually runs the board today. Every
line below was checked against the code and the schema on this branch
(`origin/main` @ `dabcd8f`), not against prose in `docs/interface-v1.md` or
`docs/messages-and-runners.md` — where a design doc and the running CLI
disagreed, the CLI's real behavior is what's recorded here, per the
handover note under T-216: *"every 'the contract says X' entry deserves the
same treatment where a real artifact exists to check against."*

This is a map and a proposal, not an edit. Nothing in `openapi.yaml` or
`dependent-notes.md` is touched here. Amendments below need master sign-off
before anyone edits the frozen contract.

---

## 1. Record shape

### `Ticket`

| contract (`openapi.yaml`) | CLI (`tickets.py: create()` / ticket JSON) | note |
|---|---|---|
| `state` (`TicketState` enum) | `status` (free string: `open/claimed/review/done/blocked`) | name differs; CLI values map onto the enum, but nothing enforces that today — `status` is never validated against a closed set. |
| `dependencies` (array of `TicketId`) | `deps` | name differs. |
| `outcome` (required to create, ≤4000 chars) | `body` (defaults to `""`, optional) | name differs **and** the requiredness is inverted — see §2 Create. |
| `created_at` / `updated_at` | `created` / `updated` | name differs. |
| — no field — | `notes` (array of `{by, at, text}`) | CLI keeps its update history inline on the ticket; contract splits this into the separate `TicketUpdate` record (see below) with its own required fields. |
| — no field, `additionalProperties: false` — | `priority`, `epic`, `sprint`, `needs` | **no field named any of these four** in `openapi.yaml` (a literal "zero hits" grep does not reproduce -- `epic` and `needs` each appear once, as unrelated prose/identifiers, not as `Ticket` properties; `additionalProperties: false` makes all four unrepresentable regardless). Sprint/epic grouping and `tickets next`'s priority-1-vs-routine ordering are unrepresentable in the contract as frozen — every ticket routed by priority today (e.g. this one) has no server-side equivalent. |
| `dependency_blocked` (derived boolean) | — no field; blocked is folded into `status: blocked` — | contract's decision #2 in `dependent-notes.md` deliberately keeps this derived rather than stored; CLI has no separate concept at all — a dependency-blocked ticket and an open one are indistinguishable in the CLI today except by walking `deps`. |
| `owner`: `AgentId` (`^agt_[0-9a-z]{8,32}$`), nullable | `owner`: free string (`alice`, `codex`, `sonnet-qa`, ...) | typical CLI owner values fail the contract pattern. See §1 Identity. |
| `id`: `TicketId` (`^[A-Z][A-Z0-9]{1,15}-[0-9]{1,6}$`) | `T-NNN` (zero-padded 3 digits, `_alloc` in `tickets.py:247`) | CLI ids conform (`T-216` matches the pattern) — no mismatch here, unlike owner. |

### `TicketUpdate` (not called out in the original ticket body — found while verifying the `Ticket` mapping above)

| contract | CLI (`notes[]` entry) | note |
|---|---|---|
| `author` (`Actor`) | `by` (free string) | name differs. |
| `body` (≤4000 chars) | `text` | name differs; contract caps length, CLI does not (legacy-import audit found notes over 4000 chars and had to invent a split-and-rejoin scheme to archive them). |
| `created_at` | `at` | name differs. |
| `superseded` (boolean, for late updates from a superseded session) | — no field — | CLI has no session concept, so nothing can be "superseded" — every note is accepted from whoever calls `tickets note`/`update`, whether or not they still hold the ticket. |
| `id` (`^upd_[0-9a-z]{8,32}$`) | — no field, updates are unindexed array entries — | CLI notes cannot be addressed individually. |

### Identity: `AgentId` / `SessionId`

- `AgentId` pattern is `^agt_[0-9a-z]{8,32}$` (`openapi.yaml:165-167`). Every
  CLI owner in production is a free-form name (`alice`, `codex`,
  `sonnet-qa`, ...) — **typical CLI names do not match the
  pattern.** This is the same shape mismatch T-213 hit on `Agent.runtime`
  during the legacy import (caught by the contract-conformance test it wrote).
- The CLI has no `SessionId` concept at all — no session lease, no
  distinction between a durable agent identity and one running process. One
  agent name can run multiple `claude -p` processes against the board
  simultaneously with nothing to tell them apart (this is exactly the T-202
  "two agents building the same ticket three seconds apart" incident logged
  in `MASTER.md`'s decision log).

### Create

- Contract `Ticket.outcome` is required to create and `CreateTicketRequest`
  additionally requires `acceptance` (422 `missing_acceptance_criteria` if
  absent — confirmed by the error's presence as a named fixture in
  `tests/fixtures/errors/`).
- CLI `create(board, title, body="", ...)` (`tickets.py:272`) needs only a
  `title`; `body` (-> `outcome`) defaults to empty and there is no
  `acceptance` field anywhere in the CLI. Every routine ticket created on this
  board today (including this one) would 422 against the frozen contract as
  written.

---

## 2. Claim / concurrency

| contract | CLI | note |
|---|---|---|
| `ClaimTicketRequest` requires `request_id`, `expected_version`, `session_id` (`openapi.yaml:1357-1374`); optional `assignment_id` for a master reservation. Optimistic concurrency — mismatch is 409 with `expected_version`/`actual_version` in `details`. | `try_claim` uses an O_EXCL lock file per ticket id (`tickets.py:538`, and the same primitive backs id allocation at `:260`). Race-safe (two callers cannot win the same lock), but there is no version field, no request-id idempotency key, and no session lease check. | Both are genuinely race-safe under concurrent claims — the CLI's mechanism is just a different (older, coarser) primitive than the contract's, not a broken one. The gap is idempotency (`request_id` replay) and the session-lease tie-in, neither of which the CLI has. |
| No reopen route in the contract at all. A rejected review returns the ticket to `claimed` per the review-decision flow (`ReviewDecisionRequest.decision: reject`), not to `open`. | `cmd_reopen` (`tickets.py:2294-2302`) sets `status` back to `open`, clears `owner`, and deletes the lock file unconditionally — usable on any ticket in any state, not just a rejected review. | `MASTER.md`'s own HANDOVER section (item 2) instructs the master to `tickets reopen` a silent claim — this is core to how the board recovers today, but it has no contract equivalent and does not distinguish "review rejected, go back to the same owner" from "claim timed out, anyone may take it now." |
| `ReviewDecisionRequest` requires `evidence_sha` to equal the SHA pinned on the submitted review, and the reviewer must differ from the submitter (`dependent-notes.md` decisions #3 and T-180 non-obvious requirement "the master cannot accept its own implementation"). `Sha` is presumably `^[0-9a-f]{40}$` (full git SHA) given `Review`/`GitEvidence` are its only real callers. | `cmd_done` (`tickets.py:1755`) has no reviewer/submitter identity check of any kind — anyone can run `tickets done` on a ticket in `review` status, including the submitter themselves, and `--force` bypasses even the branch/dirty-tree guard. `cmd_review` (`tickets.py:1196`) records `stamp = "%s@%s" % (branch, short_sha)` — a **short** sha (`git rev-parse --short HEAD`) paired with a branch name, never a bare 40-hex commit. | This is the same finding the importer hit and archived rather than fabricated: **legacy `commit` values are typically `branch@shortsha`, and `pr` is a bare issue number, not a URI** — neither can legally populate `GitEvidence` under the frozen schema. Archive verbatim and report the mismatch rather than invent a sha. This map treats that as settled: **CLI evidence format and contract `GitEvidence` are incompatible today**, not just differently named. |

---

## 3. Legacy-writer boundary

`dependent-notes.md:142-144` (T-179 section): *"reject direct legacy writes
while server ownership is active with 409 `legacy_writer_active`."* This
assumes a moment where "the server" becomes authoritative and the legacy
writer (the CLI) is turned off or made read-only.

Nothing in `tickets.py` or the board today implements or even models that
handover — the CLI writes `.tickets/*.json` directly and unconditionally, and
every E-010 lane (including this one) is *currently building the server on
the same board the CLI is actively mutating*. There is no flag, lock, or
mode-switch anywhere that a future server could check to refuse a legacy
write. **This needs its own decision, not just a schema note**: does T-180
ship with a literal CLI freeze date, a dual-write bridge, or a read-only CLI
mode? Recorded here as unresolved; T-215 (rotate/cap board growth) and T-212
(bound `tickets.py` growth) are adjacent but do not answer this.

## 4. Messaging / broadcast

`dependent-notes.md:255` (T-187 section, non-obvious requirement under
"Waking rules"): *"There is no global broadcast fanout"* — a DM/mention wakes
one agent, an unaddressed channel task wakes that channel's
`designated_master`, and "ordinary conversation reaches subscribers without
launching every agent."

CLI `cmd_msg` (`tickets.py:2522`) posts with `to=a.to or ""` — an empty `--to`
is a literal broadcast to every agent, tracked via a `broadcasts` count in
each agent's pending-work state (`tickets.py:2784-2799`, `:3458-3459`) and
folded into whether an agent is considered to have "pending work" at all
(`:2842`). There is no channel or membership model underneath it — every
agent using `tickets msg` without `--to` today (several messages in this
session's own inbox included) is doing something the contract has no route
for and does not want done at V1 (T-187's design explicitly rules out global
fanout to keep a channel from launching every agent on an unaddressed post).

## 5. Overview counts

`openapi.yaml:1079` (`OverviewCounts`, presumably): `required: [ready,
in_progress, awaiting_review, blocked]`. `ready` here means "open and not
dependency-blocked" — a ticket becomes `ready` only once nothing it depends
on is outstanding.

The CLI's nearest concept is `status == "open"`, which says nothing about
dependency state — an open ticket blocked on three others reads identically
to one with no deps at all until something walks the graph (`tickets next`,
`tickets graph`, or `unblocked()` in `tickets.py`, do so internally, but no
CLI subcommand surfaces a `ready` count as such). Any dashboard built
straight off `openapi.yaml`'s `ready` semantics will show a different number
than `tickets board`'s "N open" line for as long as both exist side by side.

## 6. Master authority credential

Deferred by the planner (12:50Z, folded into this ticket): the contract's
`securityScheme` is `operatorSession`-only for lease/pause/assign/review
decisions — there is no master token and no promotion grant modeled at all.

`tickets master take` (`tickets.py:2070-2075`) is unauthenticated by
construction: it `whoami()`s the caller (an arbitrary `TICKET_AGENT` string)
and writes `{"owner": <that string>, "since": now()}` to the master-state
file — no check against any existing master, no credential, nothing stopping
two agents from taking master seconds apart (the exact failure mode
`MASTER.md`'s HANDOVER section is written to recover from, not prevent).
**Whether T-182's master/assignment loop wants a third credential type, or
whether "any registered agent may become master, but only the operator
session can authorize destructive review/lease actions" is the intended
split, is a product decision for whoever owns T-182 — this map only confirms
that today's CLI has no analogue to gate against.**

## 7. Operator sign-in route

Deferred by the planner (12:50Z): V1 bootstraps the operator session out of
band (T-180's `operator-session.json` plus a one-time `?token=` on the UI
root) — honest on loopback only. There is no `POST /session` or equivalent
mint route in `openapi.yaml`. A shared (non-loopback) server needs a real
sign-in route; this is out of scope for V1 per `dependent-notes.md`'s closing
section ("Anything about hosting, TLS termination or remote exposure. V1
binds loopback.") but should be named explicitly as future-amendment, not
silently assumed solved.

## 8. Hook wire fields: `hook_schema_version` / `occurred_at`

From T-214's real-payload capture (merged, tickets `main` @ `680c5c4` /
`origin` @ `ba48b3d`): real Claude Code hook payloads carry **no**
`hook_schema_version` and **no** `occurred_at`/`timestamp` field — absent
entirely, not empty or null.

Checked against the frozen schema directly for this map: `HookEvent`
(`openapi.yaml:660`) requires `occurred_at` and has `additionalProperties:
false` with no `hook_schema_version` property anywhere in the object —
**the contract never had a `hook_schema_version` field to begin with**; that
assumption lived only in the design-doc prose T-178/T-181/T-188 were built
against, not in the frozen schema itself. `parse_claude_hook_event` already
tolerates the absence on the wire side (per T-214's note), so nothing is
broken today. Two things this map records so the next reader doesn't have to
rediscover them:

1. **`occurred_at` on the internal `HookEvent` record is not read off the
   wire — it must be synthesized by the adapter at receipt time**, since the
   raw payload has nothing to synthesize it from. If a dashboard or audit
   trail ever implies `occurred_at` was reported *by the agent*, that's
   false; it is receipt time at the adapter, full stop. Contract prose should
   say this plainly rather than leaving it to be inferred from a required
   field name that sounds like agent-reported data.
2. **`hook_schema_version` should be treated as correctly absent, not
   "dropped."** It was never a contract field; the fix here is documentation
   (strike the assumption from `docs/interface-v1.md`/`messages-and-runners.md`
   prose wherever it's implied), not a schema change.

## 9. Review evidence has no repo identity (T-215 — folded in per the planner's 17:20Z note on this ticket)

This is the same disease as §2's `GitEvidence` finding, one level up: the CLI's
`commit` field is an **unqualified** `branch@shortsha` — no remote URL, no
repo root, nothing that says *which* git repository the sha is meaningful in.
`cmd_review`/`cmd_done` (`tickets.py:1219`, `:1784`) both compute
`stamp = "%s@%s" % (branch, sha)` from whatever repo the caller happened to
run the command in, with no repo tag attached.

When the CLI is driven from a different clone than the deliverable, this is
not hypothetical: **`cmd_merge` can close a ticket after an unrelated merge**
because a commit in repo A can trivially "contain" a sha that was never
built there, and the merge logic (ancestry-only) had no repo field to check
against. Record the artifact repo with the sha.

**This is not a separate, smaller problem from the `GitEvidence`/SHA-shape
mismatch in §2 — it's the same root cause wearing two hats.** `GitEvidence`
needs a real 40-hex sha *and* a repo identity to be trustworthy evidence at
all; fixing only the hex-shape half (item 7 below) without also carrying repo
identity reproduces T-215's exact failure mode inside the new schema. Any
amendment to how review evidence is represented — CLI-side or contract-side —
must carry repo identity alongside the sha, not the sha alone. Folded into
amendment item 7 below rather than left as a separate line, since splitting
them risks fixing the shape and missing the identity a second time.

---

## Amendment list

| # | Item | Who changes | Why |
|---|---|---|---|
| 1 | Ticket field names (`state`/`dependencies`/`outcome`/`created_at`/`updated_at` vs `status`/`deps`/`body`/`created`/`updated`) | Whichever side T-180 chooses as the real wire format — most likely the **CLI's storage layer** adopts contract names internally (T-179/T-213 already build a separate DB, not a `.tickets/*.json` passthrough), leaving the flat-file CLI as a legacy import source only. | Two names for the same fact invite exactly the kind of silent divergence T-207's planted defect exploited. |
| 2 | `priority`/`epic`/`sprint`/`needs` have no contract field | **Contract** — add them (as optional, not required) to `Ticket`, or explicitly declare sprint/epic/priority/needs-based routing out of scope for V1's API and CLI-only forever. | Without one of these, `tickets next`'s priority routing and every sprint/epic view in `tickets map`/`tickets sprint show` cannot be reproduced by a client speaking only the contract. |
| 3 | `outcome`+`acceptance` required to create vs CLI's title-only create | **Product decision, not a mechanical fix** — either the CLI-side workflow starts requiring an outcome statement and acceptance criteria (a real behavior change for every agent on this board), or the contract relaxes `CreateTicketRequest` to make both optional with a documented default. | Every routine ticket created via `tickets create` today would 422 the frozen contract as written; this has to be picked, not split the difference silently. |
| 4 | `AgentId` pattern (`^agt_...`) vs free-form CLI owner names | **Contract, if the CLI's naming convention survives into V1** — either relax the pattern to accept the board's real names, or accept that migrating to the contract means renaming every human-readable agent identity (`alice` -> `agt_...`) at cutover, which touches every brief, hook, and message on the board. | Typical CLI names do not match `agt_*` today; this is not a rounding error. |
| 5 | No session-lease / `request_id` idempotency in the CLI claim path | **CLI, if it stays live alongside the server** — or accept that claim-idempotency and session-lease enforcement are server-only features the legacy CLI never gets, and say so. | The CLI's O_EXCL lock is race-safe but replay-unsafe; a retried `tickets claim` after a dropped response has no idempotency key to detect it was already granted. |
| 6 | `reopen` has no contract route; reject returns to `claimed` in the contract vs `open` in the CLI | **Contract** — add an explicit reopen/release route distinct from review-rejection, since `MASTER.md`'s own recovery procedure depends on exactly this operation today. | Recovery from a silent/timed-out claim is core, documented board behavior (HANDOVER step 2) with no contract analogue at all. |
| 7 | `GitEvidence`/review evidence: pinned 40-hex SHA + reviewer≠submitter vs CLI's `branch@shortsha` string + no identity check, **and no repo identity at all attached to the sha (§9 / T-215)** | **CLI, if evidence is to be trusted by a server** — a resolver step that turns `branch@shortsha` into a real 40-hex SHA (T-213 already stubbed a `sha_resolver` hook for this) is required before any `commit` value can populate `GitEvidence`; the reviewer≠submitter rule needs a real identity check added to `cmd_done`, which the CLI does not have today (anyone can close their own review); **and the sha must carry a repo identifier (remote URL or repo root) alongside it, not just the hex value** — a 40-hex sha with no repo tag reproduces T-215's false-close bug one layer up, just with a longer sha. | Legacy commit values typically fail the 40-hex pattern. Fabricating a SHA to satisfy the schema was explicitly rejected as worse than the mismatch. The repo-identity gap is the same class of failure as closing a ticket from an unrelated clone — fold it in here rather than treating it as separate from the sha-shape problem. |
| 8 | Legacy-writer handover (409 `legacy_writer_active`) has no CLI-side trigger or mode | **Both** — needs a named decision (freeze date / dual-write bridge / CLI read-only mode), not a schema tweak. | The server can be built against a board the CLI is still mutating; nothing today models "server ownership is active." |
| 9 | Broadcast messaging (CLI) vs no global fanout (contract) | **CLI usage, if messaging migrates to T-187's model** — `tickets msg` without `--to` needs a channel-scoped replacement (or an explicit "board-ops channel" convention) before broadcast can be dropped without silencing coordination traffic that currently relies on it. | T-187's no-fanout rule is a deliberate design call (don't wake every agent on an unaddressed post) that the CLI's actual daily use directly contradicts. |
| 10 | Overview `ready` (dependency-aware) vs CLI `open` (not dependency-aware) | **Contract, documentation only** — no schema change; state plainly in dependent-notes that dashboard "ready" and CLI "open" counts will differ and why, so nobody files it as a bug. | Two different, both-correct numbers looking like a discrepancy wastes triage time otherwise. |
| 11 | Master-authority credential: no third credential type vs CLI's unauthenticated `master take` | **Master's call (T-182 owner)** — decide whether V1 needs a distinct master credential or keeps "any registered agent, operator session gates the destructive actions." | Deferred by the planner; recorded here as still open, not resolved by this map. |
| 12 | Operator sign-in / session-mint route absent from the contract | **Contract, deferred** — out of scope for V1 (loopback-only), but should be named as a known future amendment rather than silently assumed solved when a shared server is discussed. | Deferred by the planner; recorded, not actioned. |
| 13 | `hook_schema_version` assumed on the wire by design-doc prose; absent from both the real payload and the frozen `HookEvent` schema | **Documentation only, both design docs** — strike the assumption from `docs/interface-v1.md`/`messages-and-runners.md` wherever implied; no schema change needed since the contract never had the field. State explicitly that `occurred_at` on `HookEvent` is adapter receipt-time, not agent-reported. | Ground-truthed by T-214's real capture; the contract already got this right, the prose around it didn't. |

## Method note

Every claim above cites a file and line (or an exact `git log` sha) that was
read on this branch while writing this map, not carried forward from the
ticket body unverified — several line numbers in the original ticket body no
longer matched this checkout (the functions had moved), so each was
re-located by name/grep and re-confirmed rather than assumed correct. Two
findings are new relative to the original ticket body: the `TicketUpdate`
field-name mismatch (§1) and the confirmation that `hook_schema_version` was
never an actual contract field (§8) — both surfaced by reading the schema
directly rather than trusting a paraphrase of it.
