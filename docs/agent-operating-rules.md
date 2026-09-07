# Agent operating rules

Rules for agents working a board. Copy this into a shared brief, or reference
it from there. Nothing here is board-specific.

## 1. Model tier is part of the task, not a detail

Route by task, not by importance:

    search / grep / read / verify a fact   -> cheapest capable model
    all coding, all data work, all SQL     -> mid-tier
    design, review, contested decisions    -> strongest model

If you are doing pure retrieval on the top model, that is a routing mistake
upstream of you. Say so in your report.

## 2. Do not poll a long-running job

Every poll re-reads the agent's whole context, so the cost is
`context x polls`, and the poll itself produces nothing.

Start the job, record its handle in a ticket note, and **stop**. A parked agent
costs nothing; a polling agent costs more than the work. Where the harness
supports it, use a monitor with an until-condition. Otherwise hand the handle
back to the coordinator and let the ticket be resumed.

## 3. One worktree per agent. No exceptions.

Two agents on the same worktree will edit the same files. If you are handed a
worktree that another live ticket names, **say so and stop.**

## 4. Never pipe a tool's output through `tail`, `head`, or `grep`

A CLI that is careful about failure puts the *reason* on one line and the
*warning* on another. Truncating the output throws away exactly the half you
need. Worse, when the rejected argument is free text, the error echoes that text
back, so a truncated view can read like your own command succeeding.

Run the command bare. If the output is genuinely too long, read it and then
narrow, rather than narrowing blind.

## 5. Context is the bill

Cost is `context size x turns alive x model rate`. Cache reads of old context
often dominate spend.

- **One task per session.** Clear between tasks. A session that never resets
  pays for its entire history on every turn.
- **Keep large tool output out of context.** Anything you read is re-read on
  every subsequent turn. Aggregate in a script and print a summary.
- **Delegate reads.** A subagent is a context firewall: it holds the file dumps
  and the failed attempts, you get back a page.
- **Resume messages should be short.** The agent still holds its context;
  re-explaining costs a cache write for nothing.

## 6. Environment pre-flight

Check these before the first real command:

- **Shell state does not persist between calls** in most harnesses. Do not fight
  it with repeated `cd`. Use absolute paths, or `git -C <dir>`.
- **Raise command timeouts explicitly** for builds and long queries.
- **Foreground `sleep` may be blocked.**
- **macOS is BSD, not GNU.** No `cat -A`; `find` and `sed` flags differ. `rg`
  may not be installed.
- **Do not put working state in `/tmp` or `/private/tmp`.** The OS may clean it
  mid-session.
- **Read the exact bytes before an edit.** "String to replace not found" usually
  means you edited against remembered content.
- **First push is `git push -u origin <branch>`.**

## 7. Report corrections, not agreement

Agents that report "as expected" are less useful than agents that say "your
figure is wrong, here is the measurement."

When you finish, state explicitly: what you built, where you disagreed with the
brief, and any number in the brief you found to be wrong.

## 8. Search for the prior art before you build

Building feels like progress and searching feels like delay, so the search gets
skipped exactly when the thing is most likely to exist.

Four commands, in order, then stop when you hit something:

1. In-repo docs next to the package — `*_usage.md`, `CONTEXT.md`, `README`.
   If a shipped doc contradicts your brief, the doc wins.
2. The tool's own surface — the FULL `--help` verb list, and `<verb> --help`.
3. The whole existing enum/const/spec block before adding a member.
4. The nearest sibling. Mirror the analogue rather than inventing a second
   pattern.

**Coordinators: run steps 1-4 BEFORE dispatching.** A brief that tells an agent
to build a procedure commits their whole session to it.

When it genuinely does not exist, say where you looked.
