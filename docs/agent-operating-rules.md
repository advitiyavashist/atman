# Agent operating rules

Rules for agents working a board, derived by mining every agent transcript from
one squad session (2026-09-07) rather than from taste. Each item below cost at
least one real agent at least one wasted turn, and several cost real money.

Nothing here is board-specific. Copy this into your shared brief, or reference
it from there.

## 1. Model tier is part of the task, not a detail

Measured spread on one session: **30-100x per turn** between tiers.

| what the agent did | model | turns | cost |
|---|---|---|---|
| built a 7-aggregate feature set | Sonnet | 308 | $26 |
| built 4 features + tests | Sonnet | 271 | $30 |
| ran Databricks queries | **Opus** | 64 | **$37** |
| read 12 files | Opus | 8 | $4 |
| repo-wide `find` / `Read` sweeps | **Haiku** | 40-82 | **$0-1** |

A 64-turn Opus query-runner cost more than a 308-turn Sonnet build. Route by
task, not by importance:

    search / grep / read / verify a fact   -> Haiku
    all coding, all data work, all SQL     -> Sonnet
    design, review, contested decisions    -> Opus

If you are doing pure retrieval on the top model, that is a routing mistake
upstream of you. Say so in your report.

## 2. Do not poll a long-running job

One agent spent **275K tokens and 138 tool calls** polling a job, then parked
without finishing. Every poll re-reads the agent's whole context, so the cost is
`context x polls`, and the poll itself produces nothing.

Start the job, record its handle in a ticket note, and **stop**. A parked agent
costs nothing; a polling agent costs more than the work. Where the harness
supports it, use a monitor with an until-condition. Otherwise hand the handle
back to the coordinator and let the ticket be resumed.

## 3. One worktree per agent. No exceptions.

Two agents were pointed at the same worktree and both edited the same file.
Recovery took a `git checkout` plus hunk reapply, and one agent's work was
dropped and restored from a backup. Nothing was lost, but the coordination cost
more than a second worktree would have.

This is a coordinator error, not an agent error. If you are handed a worktree
that another live ticket names, **say so and stop.**

## 4. Never pipe a tool's output through `tail`, `head`, or `grep`

This one bit the coordinator, twice in ten minutes, and produced two bogus bug
reports before anyone checked.

A CLI that is careful about failure puts the *reason* on one line and the
*warning* on another. Truncating the output throws away exactly the half you
need. Worse, when the rejected argument is free text, the error echoes that text
back, so a truncated view can read like your own command succeeding.

Run the command bare. If the output is genuinely too long, read it and then
narrow, rather than narrowing blind.

## 5. Context is the bill

Cost is `context size x turns alive x model rate`. On the session that produced
these notes, **66.7% of all spend was cache reads** — re-reading context, not
generating anything. Output tokens were 11%.

So:

- **One task per session.** Clear between tasks. A session that never resets
  pays for its entire history on every turn.
- **Keep large tool output out of context.** Anything you read is re-read on
  every subsequent turn forever. Aggregate in a script and print a summary. One
  session scanned 476 files but put only ~40 lines in context.
- **Delegate reads.** A subagent is a context firewall: it holds the file dumps
  and the failed attempts, you get back a page. Counter-intuitively, more
  delegation is cheaper. Subagents were 2.0% of that session's spend; the long
  coordinator session was 98%.
- **Resume messages should be short.** The agent still holds its context;
  re-explaining costs a cache write for nothing.

## 6. Environment pre-flight

Check these before the first real command:

- **Shell state does not persist between calls** in most harnesses. Do not fight
  it with repeated `cd` (one agent ran the same `cd` 33 times). Use absolute
  paths, or `git -C <dir>`.
- **Raise command timeouts explicitly** for builds and warehouse queries. Three
  agents died on a 120s default.
- **Foreground `sleep` may be blocked.** Two agents burned a turn discovering
  it.
- **macOS is BSD, not GNU.** No `cat -A`; `find` and `sed` flags differ. `rg`
  may not be installed.
- **Do not put working state in `/private/tmp`.** macOS cleans it mid-session;
  it destroyed a worktree's `.git` during this session.
- **Read the exact bytes before an edit.** Four "string to replace not found"
  failures came from editing against remembered content.
- **First push is `git push -u origin <branch>`.** One agent hit no-upstream,
  then a non-fast-forward rejection: two turns for nothing.

## 7. Report corrections, not agreement

Of four peer claims checked in one session, **three needed correction** —
including two of the coordinator's own headline numbers. Agents that reported
"as expected" were less useful than agents that said "your figure is wrong, here
is the measurement."

When you finish, state explicitly: what you built, where you disagreed with the
brief, and any number in the brief you found to be wrong.
