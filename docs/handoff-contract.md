# The handoff contract

The goal is one team: **any agent can take any seat, including
master, and be productive from one read.** No agent is special, no context is
private, and no handover depends on a conversation that happened to still be
in someone's window.

That only works if the board's documentation is genuinely load-bearing. This
file says what must be true, and `scripts/handoff-check.py` verifies it.

    python3 scripts/handoff-check.py     # run BEFORE you clear, compact, or hand over
                                         # exit 0 = safe, 1 = do not clear yet

## The onboarding path is three files, and it is a hard budget

A fresh agent taking master should be operational after:

    .tickets/MASTER.md      mission, current state, workforce, rules, comms
    HANDOFF.md              the technical state of the actual work
    tickets map             the live graph

Plus `.tickets/briefs/_shared.md` before touching code. Nothing else unless a
specific ticket points there. Team knowledge
([docs/knowledge/](knowledge/README.md)) is **not** a fourth onboarding
file — pull a doc when the work needs it. The operator walkthrough for a
fresh master session is [onboarding/master-howto.md](onboarding/master-howto.md).

**Three files is a budget, not an aspiration.** If onboarding needs a fourth,
the first three are not saying enough — fix them rather than adding to the
reading list. Every file added to the path is a file that will go stale.

## What each file owes the next reader

**MASTER.md — how to coordinate.** Mission in one paragraph. Where things stand
*right now*. The workforce table with models. What master owns and what it must
not do. Dispatch rules. Comms conventions.

**HANDOFF.md — what is true about the work.** What shipped and what is in
review. Decisions taken, so nobody relitigates them. **Retracted numbers, named
as retracted.** What is broken in the environment. Which doc to read for what.

**Ticket `done --notes` — what a dependent ticket must match.** Paths, names,
decisions. Written for the next agent, not for the log.

## Two failure modes that are invisible without a checker

Neither of these is caught by tests, linting, or code review — they are
properties of documentation, not of code.

### 1. A file that exists but was never filled in

`MASTER.md` sat as install-time template text for a whole session. Its Mission
read `(what we are building, one paragraph)`; its workforce table listed
`example`. Every agent is told to read it first, so every agent read a stub, and
nobody noticed because **the file existed and was non-empty.**

`handoff-check` FAILs on any surviving `MASTER_TEMPLATE` line.

Related and worth its own rule: an **append-only decision log is not a state
section.** A log accumulates superseded claims, and a reader cannot tell which
are live. That board's log still asserted "aggregates are the bottleneck" long
after aggregates shipped. So keep a current-state section and **say in the file
that it wins over the log.** The checker WARNs when a log exists without one.

### 2. Documentation that is not recoverable

`.tickets/` was gitignored wholesale, so `MASTER.md` was untracked. Overwriting
it destroyed twelve decision-log entries with no recovery path; they survived
only because one session happened to have read them minutes earlier.

**Live board state should be ignored. Hand-written docs must be tracked.** The
distinction is state versus memory, and it does not follow the directory
boundary.

    .tickets/*                      # locks, per-ticket JSON, identities: ignore
    !.tickets/MASTER.md             # memory: track
    !.tickets/AGENT-HANDLES.md
    !.tickets/AUTOMATION-BUDGET.md
    !.tickets/briefs/

Note the pattern is `.tickets/*`, **not** `.tickets/`. A trailing-slash
directory ignore stops git descending at all, so the negations are never
reconsidered and silently do nothing.

`handoff-check` FAILs on any required doc that is untracked, and on any that is
tracked but has uncommitted changes — a tracked-but-uncommitted doc is exactly
as lossy as an untracked one.

## Never overwrite a file you have not fully read

The destroyed log was a `Write` over a partially-read file. For any
hand-written doc:

- **Append** (`>>`, or the tool's own `log`/`note` verb) rather than rewrite.
- If you must rewrite, **read the whole file first**, and confirm it is tracked
  and committed so the old version is recoverable.
- Prefer a targeted edit to a whole-file write.

An untracked file has no undo. That is the whole reason the tracking rule above
exists.

## Record agent ids on spawn

An unrecorded agent id is the one thing genuinely lost when a coordinator's
context is compacted: the agent's accumulated context becomes unreachable and
the only recovery is a fresh spawn that re-reads every brief from scratch.
Write ids to `AGENT-HANDLES.md` **immediately**, not at the end.

Resume beats re-spawn: a stopped agent resumes from its transcript with context
intact, and does so even when it no longer appears in the addressable list.
Stopping is reversible and nearly free. `handoff-check` WARNs on any active
claim whose owner has no recorded id.

## Run the checker as a gate, not a ritual

It is deterministic, needs no model, and costs nothing. Run it before you
clear, before you hand over, and after editing any of the three files. If it
FAILs, the next agent to take the seat will be worse off than you were — which
is the one thing this contract exists to prevent.
