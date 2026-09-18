# A first session, captured

This is a real run, not a mock-up: a fresh git repo, a clone of this tool on
`PATH`, and nothing else. The only edit is that the temp directory the capture
ran in has been shortened to `~/demo`, and `python3 tickets.py` written as
`tickets`, so the lines fit. Output is otherwise verbatim.

Total elapsed: under a minute. Four commands.

---

## 1. `atm quickstart`

One command takes you from a repo with no board to a board with work on it and
you registered against it.

```console
$ cd ~/demo
$ atm quickstart --agent alice --roles backend
board: ~/demo/.tickets
  resolved from the git worktree cwd is in (~/demo)
wrote: ~/demo/.cursor/rules/tickets.mdc
wrote: ~/demo/AGENTS.md
wrote: ~/demo/.gitignore
wrote: ~/demo/.tickets/MASTER.md
bound: `atm` run from ~/demo resolves to this board (`tickets` is a compatibility alias).

Claude Code picks this up after `atm hooks claude --agent alice` installs
the worktree-scoped SessionStart hook.
Codex and Cursor read AGENTS.md; Cursor also gets .cursor/rules/tickets.mdc.

board: ~/demo/.tickets
epic:  E-001  Sample epic: a first slice end to end
  T-001  Sample: design the data model              ready now
  T-002  Sample: build the API on top of the model  after T-001
  T-003  Sample: put a screen on the API            after T-002
joined as alice  roles=['backend']  can=-  cost=medium  harness=claude (default)

The three commands that matter:
  TICKET_AGENT=alice atm next                      claim the next ready ticket
  TICKET_AGENT=alice atm update <id> "..."         say where you are, at least every 45 min
  TICKET_AGENT=alice atm review <id> --notes "..." hand it back with evidence

Feel the gate: atm quickstart --gate   (60s, throwaway dir: a dependent ticket
               stays shut until a DIFFERENT seat accepts the commit)

See it: atm ui             ->  the local URL it prints (read-only, auto-refresh)
Learn it: atm guide   |   docs/first-session.md   |   README.md
```

Two things worth noticing before moving on.

**`bound:`** is the tool confirming that `atm` run from this directory
resolves to the board it just wrote. It is a real check, not a pleasantry — an
`init` that writes a board somewhere your later commands will not find is the
failure this line exists to rule out. If they had disagreed, quickstart would
have refused and written nothing.

**The three sample tickets are a chain**, not three loose items: `T-002` is
`after T-001`, `T-003` is `after T-002`. That matters in step 3.

**`harness=claude (default)`** is a label on the agent record because
`--harness` was omitted. Quickstart did not start Claude. The field is used
when `atm watch` or `atm spawn` actually launches a process. A dry
join plus `atm next` in your own shell is the same: label only, no Claude
until someone watches.

## 2. `atm next` — the first claim

```console
$ TICKET_AGENT=alice atm next
[>] IN PROGRESS T-001  Sample: design the data model  (E-001; role=backend; owner=alice)

Read this briefing before editing:
  ~/demo/.tickets/MASTER.md
Epic E-001: Sample epic: a first slice end to end  [            ] 0/3
  Created by `atm quickstart` so the board is not empty on day one.
  Remove the samples with `atm quickstart --remove`.
  also in this epic: T-002 [ ]; T-003 [ ]
Waiting on this: T-002 Sample: build the API on top of the mode
Time: active 0m, waited 0m before claim, last update 0m ago

A sample ticket created by `atm quickstart`.

It has no dependencies, so it is the one `atm next` hands out first.
Work it like a real ticket: claim it, post an update, then send it to review.
Delete the samples whenever you like: atm quickstart --remove

RULE: you are on branch 'main' in the primary worktree. Work on your own tree:
  git -C ~/demo worktree add .worktrees/alice-work -b alice-work
  cd ~/demo/.worktrees/alice-work

Post progress with `atm update T-001 "..."` at least every 45 min; finish with
`atm done T-001 --notes "branch@sha, paths, decisions"`.
```

`next` did not just hand over an id. It printed the briefing files to read, the
epic and its progress, **what is waiting on this ticket** (`T-002`), and the
rule about working in your own worktree rather than on `main`. That block is
the handoff context that would otherwise live in a chat window.

## 3. `atm update` — say where you are

```console
$ TICKET_AGENT=alice atm update T-001 "sketched the schema; two tables so far"
update on T-001 recorded (0m into the task)
```

Every 45 minutes, at least. Silence is treated as a timeout, because from the
outside a thinking agent and a dead one look identical.

## 4. `atm graph` — see the shape of the work

```console
$ TICKET_AGENT=alice atm graph
Dependency graph (2 open, 1 claimed)
`- [>] T-001 Sample: design the data model  (backend; @alice)
   `- [ ] T-002 Sample: build the API on top of the model  (backend; waiting on T-001)
      `- [ ] T-003 Sample: put a screen on the API  (console; waiting on T-002)
```

The same tree is the default Work view in `atm ui` (Work → Graph): ticket ids, status, and `waiting on` edges —
not a dump of titles. `atm map` is the sprint/epic listing with the same
deps. Follow-up is `atm update` / `atm here`; silent >90m claims:
`atm reopen`; submit with `atm review` then `atm merge`.

## The thing that surprises people first

Ask for another ticket as a second agent and you get:

```console
$ TICKET_AGENT=bob atm next
no ticket ready: 2 open, all waiting on unfinished work (in progress with: alice)
```

This is not the board being empty or broken. `T-002` and `T-003` are waiting on
`T-001`, and `T-001` is in progress. The board would rather tell an agent there
is nothing to do than hand it work whose prerequisite does not exist yet —
starting too early is one of the failure modes the whole tool is built around.

Finish `T-001` and `T-002` becomes claimable.

## From here

```sh
atm review T-001 --notes "paths, tests run, decisions"   # hand it back with evidence
atm quickstart --remove                                  # delete the samples
atm ui                                                   # watch it live — Work → Graph (#graph)
atm guide                                                # connect claude / codex / cursor
```

After `atm ui`: median turns / yield stay `—` until a done ticket reports
(unknown ≠ 0).

## Team intro

The sample chain (`T-002` after `T-001`) is the same contract as a real board:

1. **Probe integrations** — `atm harness available` (missing is a row).
   Do not spawn until the operator answers.
2. **Plan the graph** — `atm plan` so JSON `deps` become real `--after`
   edges. Inspect with `atm graph`. Do not seed one `atm create` per
   title with no edges.
3. **Unattended persist** — the worker runs to a **reviewable SHA** on its
   own branch (`atm review <id> --notes "..."`).
4. **Human review is the gate** — `atm merge` is not silent
   auto-promote. Success of a node can start the next unblocked child.

Then read the worker loop and master loop sections of
[README.md](../README.md), and `atm connect` for wiring a real agent to the
board.

Taking the master seat in a fresh session:
[onboarding/master-howto.md](onboarding/master-howto.md).
