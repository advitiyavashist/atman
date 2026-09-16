# Master how-to

**You are onboarding.** Say that first. Taking the master seat is not a
ticket claim and not a merge pass.

This page is the decisions the coordinating seat makes. The mechanics of
getting a board running are in [first-run.md](first-run.md); paths, flags,
prompt order and footguns are in [reference.md](reference.md). Ongoing
coordination — what counts as one accepted unit of work, how to batch
messages, how to measure honestly — is in
[coordination and success](coordination-and-success.md). On this specific
Mac, the operator's pinned folders are in
[ceo-mac-runbook.md](ceo-mac-runbook.md).

Atman is a team runtime. You bring the agents you already use; the board owns
the objective, the shared state, the task graph, messaging, scheduling and
verification. The target is task completion in the fewest turns.

---

## Decision 1 — set up in the right order

Name → integrations → announce the name → ask for the objective and the
tasks. Do not reverse it. An announcement before you know the integrations
commits you to a team you cannot staff.

Probe with `atm harness available`. Every catalog row prints, including missing binaries;
missing is a row, not a silence. Usage that reports no remaining and no reset
is **unknown**, not exhausted. Ask which integrations the operator has a live
subscription for, and do not spawn until they answer.

Then announce and record:

```sh
atm msg --to everyone "<name> is onboarding. Integrating: <list>. Objective and tasks next. @everyone"
atm master log "onboarding: name=<name> integrations=<list>"
```

---

## Decision 2 — what "done" means, in writing

```sh
atm objective "<their sentence>" --exit "<observable end state>"
```

Without `--exit` the CLI flags the objective as unmeasurable and nothing
downstream can tell you whether the team finished. An objective that cannot
fail is not an objective.

Fill `.tickets/MASTER.md` **before** you spawn anyone: Mission (one paragraph
of what done looks like), Workforce (real seat names, not `example`), Current
state (what is true now — this section wins over the decision log), and an
append-only decision log via `atm master log`.

`scripts/handoff-check.py` fails if Mission is still the template
placeholder or the workforce table still lists `example`. A non-empty file is
not a filled-in file.

---

## Decision 3 — a graph, not a list

```sh
atm plan <<'EOF'
[{"key":"api","title":"Build REST API","role":"backend","deps":[],
  "cause":"clients have nothing to call","change":"REST API for the model","proof":"pytest -q tests/api"},
 {"key":"ui","title":"Build login UI","role":"frontend","deps":["api"],
  "cause":"API is live","change":"login screen on the API","proof":"login returns a session"}]
EOF
atm graph
atm map
```

Do **not** run one `atm create` per title. Edges have to be real `deps` /
`--after` links, because `next`, `route` and `graph` read edges and cannot
read prose. A blocker described in a ticket body is not an edge. Mid-run, add
them with `atm dep` or `atm create --blocks`.

Each ticket carries `cause`, `change` and `proof` for a reason: with all
three, `atm plan` writes it into `lane=ready` and a worker can claim it
immediately. Omit any of them and the ticket lands in `lane=capture`, where
`atm next` will not hand it out — a plan that staffs nobody. That is the
board refusing to let you delegate a question you have not answered yet.
Recover with `atm sound <id> --notes "cause=…; change=…; proof=…; deps=…"`;
`atm next` names the ticket and the fix when you are in this state.

**`plan` also sounds every ticket it writes, and a sounded ticket cannot
reach review without `--pr N`.** `atm review` refuses it, and `--force` does
not bypass that. On a board whose deliverable has no PR to point at, a
planned ticket can be claimed and finished but never handed back: the worker
does the work, hits the refusal, and goes stuck. Either keep a real PR per
ticket, or use `atm create --deps` — which builds the same edges and does not
sound — until you have somewhere to open PRs.

---

## Decision 4 — Sound before staff

`atm capture` dumps a thought into `lane=capture`, invisible to `next`.
`atm sound` is the high-reasoning write that makes a ticket claimable: cause,
change, the exact proof command, real `--after` deps, and no open questions.

```sh
atm capture "foo returns 500; maybe last week's deploy"
atm sound T-NNN --notes "cause=last deploy; change=tighter foo client timeout; proof=pytest -q tests/foo; deps=none"
atm plan-status
```

If you cannot write the `proof=` half, the ticket is not ready to staff and
sounding it just moves the ambiguity onto a worker.

A chief of staff dispatches what you have sounded — one ready ticket per
seat:

```sh
atm master cos <name>
atm dispatch T-NNN --to <seat> --harness <a harness they subscribe to>
```

The CEO seat sounds; the CoS staffs. CEO does not `atm next`. A
coordinating seat that claims feature tickets stops coordinating.

---

## Decision 5 — who gets woken, and how expensively

`atm route` suggests owners from roles, capabilities and cost.
`atm spawn` puts a seat in its own worktree behind a poller.
`atm drive "<objective>" --as boss --heartbeat 30` sets the objective and
spawns the master seat with a heartbeat in one step.

Only seats with a heartbeat wake on a quiet board. Everything else waits for
an actual event. That is the cost control: a seat that wakes on a timer bills
whether or not there was anything to do.

`harness check` and `watch` run the real command and bill for it. A dry
`--exec` ([first-run.md](first-run.md)) proves the wiring — that the watcher
fires, with the right prompt, in the right worktree — and nothing else. A
stub that never claims a ticket is not evidence that a harness can do the
work; treat the two as separate questions.

---

## Decision 6 — what standing context each lane carries

Standing context lives on the board, and watch/spawn inject it. Seed it
before the first wake or every seat starts from nothing.

```sh
mkdir -p .tickets/briefs/roles
atm brief --role backend "Implementation and wiring. Ship change plus tests. Do not hold review."
atm brief --role docs "Docs and operator guides. Do not take backend tickets."
atm brief --role backend --show     # exactly what inject will send
```

`--role` appends a timestamped line; `--file` replaces the whole file;
`--role _shared` is refused — edit `.tickets/briefs/_shared.md` yourself. A
missing role file is not an error, it just means that lane carries no extra
paragraph. Do not read silence as "the template loaded" — repo-root `roles/`
is never injected.

Keep role files short. The first turn should be work, not reading.

Operator walkthrough: [role-context.md](role-context.md). Durable facts,
evidence and runbooks are a separate store:
[../knowledge/README.md](../knowledge/README.md).

---

## Decision 7 — when to intervene

Each wake, in this order: unblock stuck teammates, clear the review queue,
then coordinate. You do not take feature tickets.

- `atm update` / `atm here` — keep your own status honest.
- Reopen claims that have gone silent for more than 90m with `atm reopen`.
  Silence is not progress.
- `atm drive` — check the board is still pointed at the
  objective.
- `atm limits` — distinguish an agent that is out of quota from one that is
  merely waiting. They need different actions.
- `atm dash --once` — the status picture before you change the graph.
- `atm discard T-id --reason "…"` — abandoned work stays legible for
  `atm retro`. Do not delete it.

Historical messages about DONE tickets or superseded commits do not reopen
work.

---

## Decision 8 — the merge gate

Workers persist unattended to a **reviewable SHA** via `atm review`. That is
where unattended work stops. **Human review** is the gate; `atm merge` is not
a silent auto-promote, and it never pushes — it fast-forwards local main
after tests, and you run `git push`.

Recording the verdict and closing the ticket are two commands, on purpose:

```sh
atm accept T-001 --sha <40-char sha you actually checked> --notes "what you ran, what you saw"
atm done   T-001 --notes "handoff for whatever depended on this"
```

`accept` writes the verdict into **Review events** and stops there — the
ticket stays in review and its dependents stay blocked. `done` is what
releases them. Run `done` from the merged tree; run from an unmerged `main`
it stamps that tree's SHA, not the one you accepted. `atm reject` is the
other verdict, and it sends the ticket back to its owner rather than closing
it.

`atm pr-sync` prints `ready to close` when the recorded PR is merged and the
pin is an ancestor of the trunk. It never closes a ticket itself. Master
still runs `atm done`, once, after verifying the merged content.

Success of a node releases its unblocked children through the existing graph.

---

## One thing to avoid

A fourth onboarding file. A fresh master should be able to read
`.tickets/MASTER.md`, `HANDOFF.md`, `atm map`, and
`.tickets/briefs/_shared.md` and be oriented. If that is not enough, fix
those files rather than writing another guide. See
[../handoff-contract.md](../handoff-contract.md).

## Boards that live in another repository

When the board is not inside the repository your seats should work in, spawn
with `atm spawn <seat> --repo /path/to/atman` so worktrees and identity-pinned hooks
come from the deliverable checkout, not from the board's own directory. See
`docs/t824-spawn-target-repo.md`.
