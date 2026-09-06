# T-224 — amendment sign-off request

Companion to `docs/contracts/cli-mapping.md` (T-211, merged `92af549`).
Decisions 8/11/12 from that map's amendment list were ruled by the planner
(18:05Z, `T-224` notes) and are already landed: see `dependent-notes.md`
decisions 9-11 and the `operatorSession` scheme description in
`openapi.yaml`. This document covers everything the planner explicitly left
**not ruled** ("items 1-7, 9, 10, 13 stand as T-211 wrote them and go through
review first... do not treat my silence on those as approval"), plus four
items T-229's completeness audit (sonnet-console) found missing from the
original map. Nothing below is applied to `openapi.yaml` yet — this is the
proposed diff the planner asked for, for item-by-item sign-off.

For each item: **recommendation**, concrete diff where one is ready to apply,
and what happens once approved.

---

## 1. Ticket field names (state/dependencies/outcome/created_at/updated_at vs status/deps/body/created/updated)

**Recommendation: no `openapi.yaml` change.** This is a T-179/T-213 storage-layer
naming question, not a contract gap — the contract already has the names it
wants. The CLI's flat-file field names become legacy-import-source-only
vocabulary once T-179's server is authoritative (decision 10 already commits
to that cutover). Nothing to sign off here beyond confirming T-179 owns the
rename, which T-213's separate DB (not a `.tickets/*.json` passthrough)
already assumes. **No diff to apply; close as "no contract action, T-179
scope."**

## 2. `priority`/`epic`/`sprint`/`needs` — no contract field

**Recommendation: add as optional fields to `Ticket`.** The planner's own
lean in this ticket's body ("Sprints and next-ordering are user-visible
product... I lean toward first-class fields") plus T-229's finding #2 below
point the same way. Proposed diff (additive — optional fields, no version
bump per README rule 2):

```yaml
    Ticket:
      properties:
        # ... existing fields ...
        priority:
          anyOf: [{ type: integer, minimum: 1, maximum: 3 }, { type: 'null' }]
          description: 1 = highest. Null = unset/routine, matching CLI's default.
        epic:
          anyOf: [{ type: string, maxLength: 100 }, { type: 'null' }]
        sprint:
          anyOf: [{ type: string, maxLength: 50 }, { type: 'null' }]
        needs:
          type: array
          items: { type: string, enum: [docker, own-machine, network, browser] }
          default: []
```

`needs` as a closed enum matches the four real values seen on this board
today (`docker`, `own-machine`, `network`, `browser` — grepped from
`.tickets/*.json` and `tickets.py`'s `--needs` help text); open it to free
string only if a fifth value shows up.

## 2b. `suggested` field (T-229 finding #2) — same bucket as #2, not named there

`cmd_route` writes `t['suggested']` (a routed-owner suggestion) with zero
contract representation. **Recommendation: fold into the same additive PR as
item 2** rather than a separate schema pass — it is the same shape of gap
(CLI-internal scheduling metadata with no API surface) and touches the same
`Ticket` object:

```yaml
        suggested:
          anyOf: [{ $ref: '#/components/schemas/AgentId' }, { type: 'null' }]
          description: Owner `tickets route` suggested; not yet claimed.
```

## 2c. `tickets route` / suggested-owner subsystem (T-229 finding #3) — new item, not folded into #2

Distinct from the *field* gap above: the whole routing mechanism
(`score_agent`, `load_workforce`, `load_roles`, `cmd_route`) has no contract
representation at all — no endpoint returns a routing suggestion, and
nothing in `dependent-notes.md`'s "deliberately does not settle" list names
it as out of scope. **Recommendation: name it explicitly rather than add a
route this pass.** This is live planner-routing behavior (visible across
this board's own message history — "planner routes X to Y") that a
dashboard-only client cannot reproduce, but a `GET /tickets/{id}/routing`
endpoint is new product surface, not a naming fix, and deserves its own
ticket with its own acceptance criteria rather than a drive-by add here.
Proposed: add one line to `dependent-notes.md`'s "does not settle" list —
*"Routing/suggested-owner computation (`tickets route`) — CLI-only for V1;
a client speaking only the contract cannot reproduce `tickets next`'s
ordering. Candidate for a future `GET .../routing` read-only endpoint."* —
and open a new ticket (not T-224) if/when a lane needs it. **Ask: sign off
the doc note now; hold the endpoint for a separate ticket.**

## 2d. Agent usage-limit state (T-229 finding #4) — new item

`cmd_limit`/`cmd_limits` (`tickets.py:1631+`) maintain a per-agent
`{at, until, note}` limit record consumed by `cmd_route` (skip a limited
agent when suggesting owners) and `cmd_watch`. Zero representation in
`AgentState` (`openapi.yaml:503-505`: `connected/idle/working/
awaiting-input/offline/revoked` — no `limited` value). Not hypothetical:
gpt-cursor and gpt-codex both hit real usage limits this session, and
**T-230's liveness-truth spec (this session, `steer` repo,
`docs/handoffs/reports/t230-liveness-truth-spec.md`) independently confirmed
the CLI's current `limited` signal is self-reported only** (written solely
by an explicit `tickets limit` call, never auto-derived) — which is exactly
why gpt-cursor's hard monthly limit rendered as a healthy `seen 4m ago` in
`tickets who`/`dash` this session: nobody had called `tickets limit` for it.
T-185 (agent recovery verification) needs this representable to assert
anything about recovery from a limited state.

**Recommendation: add `limited` to `AgentState`, additive:**

```yaml
    AgentState:
      enum: [connected, idle, working, awaiting-input, offline, revoked, limited]
```

And add the detail fields T-230's spec says a client needs to render this
usefully (mirrors the CLI's existing `{at, until, note}` shape):

```yaml
    Agent:
      properties:
        # ... existing fields ...
        limit_detail:
          anyOf:
            - type: object
              additionalProperties: false
              required: [since]
              properties:
                since: { $ref: '#/components/schemas/Timestamp' }
                until: { anyOf: [{ $ref: '#/components/schemas/Timestamp' }, { type: 'null' }] }
                note: { anyOf: [{ type: string, maxLength: 200 }, { type: 'null' }] }
                source:
                  type: string
                  enum: [manual, derived, heuristic]
                  description: |
                    manual = someone ran `tickets limit`. derived = ground-truth
                    parse (transcript/task_complete) per T-230's spec. heuristic
                    = watch-log inference only, label it as such, never render
                    as confident. See T-230's spec for what backs each value.
            - { type: 'null' }
```

The `source` enum is the direct link to T-230: this ticket's spec explicitly
requires the tool to distinguish "a human told us" from "we derived it" from
"our best guess", and the API needs the same three-way split or a dashboard
re-introduces the same confident-wrong-answer failure T-230 documents at the
CLI layer.

## 3. `outcome`+`acceptance` required to create vs CLI's title-only create

Planner: *"a genuine product call I want to make WITH the review in hand...
do not treat my silence as approval."* Two options, no default recommendation
picked here since the planner explicitly wants to make this call, not have
it pre-decided:

- **(a) Contract relaxes**: `TicketCreateRequest` drops `outcome`/`acceptance`
  from `required`, keeps them optional with documented defaults
  (`outcome: ""`, `acceptance: []`) matching today's CLI behavior exactly.
  Zero workflow change for any agent; V1 ships as everyone already works.
- **(b) CLI changes**: `tickets create` gains required `--outcome`/
  `--acceptance` flags (or prompts), matching the contract as frozen. Real
  behavior change for every agent and every brief/skill that calls
  `tickets create`.

**Ask: pick (a) or (b).** Noting for the record: (a) is reversible (can
tighten later once the board has real outcome/acceptance discipline to
migrate from); (b) is not free to undo once 100+ more tickets exist with
real acceptance criteria nobody wants to have written retroactively.

## 4. `AgentId` pattern (`^agt_[0-9a-z]{8,32}$`) vs free-form CLI names

Planner: *"I expect the pattern to widen... Argue me out of it if you
disagree."* Not arguing — the numbers make the case on their own (0/35 real
names match `agt_*`, and renaming 117+ done tickets' `owner` field plus every
brief/hook/message that references an agent by name is not a proportionate
fix for a regex). Concrete diff, checked against every real name currently
on the board (`claude-fable`, `cos-opus`, `gpt-cursor`, `sonnet-sdk`,
`agent-20592`-style fallback names included):

```yaml
    AgentId:
      type: string
      pattern: '^[a-z][a-z0-9-]{1,31}$'
      description: |
        Free-form lowercase agent identity (hyphens allowed), matching the
        CLI's real installed base — not a generated `agt_*` token. Widened
        2026-09 (T-224) rather than rename ~35 live identities and every
        ticket/brief/message that references one by name.
```

**Ask: approve this exact pattern** (verified against all live names this
session) **or supply a different one** if a stricter shape is wanted for
*new* agents while grandfathering existing names — that would need a second
field or an allowlist, which is more machinery for a problem the wide
pattern solves in one line.

## 5. No session-lease / `request_id` idempotency in the CLI claim path

**Recommendation: document, don't build.** The CLI's O_EXCL lock is
race-safe (confirmed, not broken) but replay-unsafe; adding idempotency to a
legacy writer that's read-only at cutover (decision 10) is work that never
pays off before it's retired. Proposed: one line in `dependent-notes.md`'s
"does not settle" list — *"Claim idempotency / session-lease replay
protection on the legacy CLI path — server-only per decision 10; the CLI
does not get this before going read-only."* **Ask: sign off the doc note;
no schema change.**

## 6. No `reopen` route in the contract

Contract's reject-review path returns to `claimed`, not `open`; CLI's
`cmd_reopen` returns to `open` unconditionally from any state and is core to
`MASTER.md`'s recovery procedure (HANDOVER step 2). **Recommendation: add the
route**, additive:

```yaml
  /tickets/{ticket_id}/reopen:
    post:
      tags: [tickets]
      summary: Release a claim (timeout/stuck recovery), independent of review.
      security: [{ operatorSession: [] }]
      requestBody:
        content:
          application/json:
            schema:
              type: object
              additionalProperties: false
              required: [request_id, expected_version, reason]
              properties:
                request_id: { type: string }
                expected_version: { type: integer }
                reason: { type: string, maxLength: 500 }
      responses:
        '200': { description: Reopened -- state open, owner cleared. }
        '409': { description: Version mismatch. }
```

Gated on `operatorSession` (a master/operator action), separate from
`ReviewDecisionRequest.decision: reject` (which stays `claimed`) — these are
two different real operations today (timeout recovery vs. review
disposition) and collapsing them into one route was the original design gap.
**Ask: approve route + who may call it** (operator-only as drafted, or also
any agent reopening their own stale claim?).

## 7. `GitEvidence`/review evidence: SHA shape + reviewer identity + repo identity

**Correction to T-211's original framing (T-229 finding #1, verified against
`openapi.yaml:385` myself before writing this):** `GitEvidence.repository`
**already exists** — `{ type: string, maxLength: 200 }`, currently optional.
T-211's §9 narrative reads like repo identity is an absent concept; it is
not. **No new field needed here** — only two real changes:

- **Promote `repository` from optional to `required`** in `GitEvidence`.
  This IS breaking (optional → required) — needs the `info.version` bump
  README's freeze rules require for this class of change.
- **Relax `sha`'s pattern**, since 115/115 real CLI `commit` values are
  `branch@shortsha`, not 40-hex. Two sub-options:
  - **(a)** Accept `branch@shortsha` shape directly in `Sha` (loses the
    "pinned exact commit" guarantee a short hash technically weakens, but
    matches what the CLI can produce today with zero new code).
  - **(b)** Keep `Sha` at 40-hex, require a resolver (T-213 already stubbed
    a `sha_resolver` hook) that turns `branch@shortsha` into a real 40-hex
    SHA before populating `GitEvidence` — stronger guarantee, real
    implementation work, and a resolver can fail (branch deleted, ambiguous
    short hash on a large repo) in ways (a) cannot.

Planner's own words apply directly here: *"this one must ALSO carry repo
identity, not just relax the sha... relaxing the pattern without adding repo
identity fixes the validation error and leaves the actual bug armed."*
Making `repository` required addresses exactly that — a `branch@shortsha`
with a mandatory repo tag is no longer the T-215-class ambiguous reference,
regardless of which sha option is picked. **Ask: (1) confirm `repository`
required — recommended, low-risk since the field already exists and this
session found no code path currently omitting it; (2) pick (a) or (b) for
the sha shape** — recommendation is (a) for V1 (matches decision-4's
reasoning: don't build resolver machinery before the CLI needs one), with
(b) as the natural tightening once T-179's server is the sole writer.

Also note for the record, `pr` (CLI: bare issue number) vs `pr_url`
(contract: `format: uri`) is the same class of gap and should ride the same
PR: either the contract accepts a bare number alongside a URI, or CLI-side
gains a URL template. Not separately numbered — same root cause as this
item's sha-shape mismatch (CLI produces the human-readable form, contract
wants the machine-precise one).

## 9. Broadcast messaging (CLI `--to`-less `tickets msg`) vs no global fanout (contract)

**Recommendation: document as CLI-only, do not add to V1 API.** T-187's
no-fanout rule is deliberate; several messages in this board's own history
(including this session's) use broadcast for genuine board-wide status
("idle:", "REVIEW method notes") that a channel model may or may not want to
carry over as-is. Proposed `dependent-notes.md` addition: *"CLI broadcast
(`tickets msg` with no `--to`) has no contract equivalent and is not
carried into T-187's channel model automatically — a lane building the
message API should treat every historical broadcast as CLI-legacy, not as
evidence the API needs an unaddressed-fanout route."* **Ask: sign off the
note; this is documentation only, unless the planner wants an explicit
board-ops-channel convention specified now** (that would be new scope, not
a naming fix, and probably belongs to T-187 directly, not T-224).

## 10. Overview `ready` (dependency-aware) vs CLI `open` (not dependency-aware)

**Recommendation: documentation only, exactly as T-211 proposed** — no
schema change. Add to `dependent-notes.md`: *"Dashboard `ready` (open AND
not dependency-blocked) and `tickets board`'s 'N open' count are both
correct and will differ for as long as the CLI and server run side by side;
this is not a bug in either."* **Ask: sign off the note.**

## 13. `hook_schema_version` / `occurred_at` prose

**Recommendation: documentation only, exactly as T-211 proposed** — the
contract already has this right (`occurred_at` required on `HookEvent`, no
`hook_schema_version` field ever existed). Strike the `hook_schema_version`
assumption from `docs/interface-v1.md`/`docs/messages-and-runners.md`
wherever implied, and state plainly that `occurred_at` is adapter
receipt-time, not agent-reported. **Ask: sign off; T-181 (hook adapter
owner) should carry out the actual doc edits in those two files since T-224
does not own them.**

## TicketUpdate field names + length cap + note ordering

(T-211 §1's `TicketUpdate` finding, not in the original numbered list but
load-bearing — folding it into this sign-off pass since it shares item 1's
root cause.) Contract's `(author, body, created_at, superseded, id)` vs
CLI's `(by, text, at)` — same rename-at-storage-layer answer as item 1,
**plus two things item 1 didn't have**: `body` has a 4000-char cap the CLI
does not enforce (2 real notes exceed it per T-213's legacy-import audit),
and CLI notes are unordered array entries with no `id`, so 59 real notes
sharing one `at` timestamp have no stable order today.

**Recommendation:**
- Length: **raise the cap**, not truncate or split. T-213 already had to
  invent a split-and-rejoin scheme to archive 2 oversize notes during legacy
  import — that scheme working proves splitting is *possible*, not that it's
  *desirable* going forward; a review note is prose a human or agent wrote
  once, splitting it back into multiple `TicketUpdate` rows on write makes
  every future reader reassemble it. Propose `maxLength: 20000` (covers the
  9000-char notes T-213 found with headroom, without being unbounded).
- Ordering: add an explicit `ordinal` (monotonic per-ticket integer) to
  `TicketUpdate`, additive. Fixes the 59-notes-share-an-`at` case without
  requiring sub-second timestamp precision everywhere.

**Ask: approve `maxLength: 20000` (or a different number — say which and
why) and the `ordinal` field.**

---

## Summary for the planner

Ready to apply on approval (concrete diffs above, this session already
verified each cited fact against the live schema/CLI): **2, 2b, 2d, 4, 6,
7's `repository`-required half.** Needs a pick from two-or-more named
options: **3, 7's sha-shape half, TicketUpdate cap/ordinal.** Documentation-
only, no schema change: **1, 2c, 5, 9, 10, 13.**

Please reply per item (a ticket id, an option letter, or "approved as
drafted" is enough) — I will not touch `openapi.yaml`/`dependent-notes.md`
further on any item without an explicit answer against it, matching the
"per-amendment, not a blanket unfreeze" instruction this ticket opened with.
