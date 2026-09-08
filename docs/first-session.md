# A first session, captured

This is a real run, not a mock-up: a fresh git repo, a clone of this tool on
`PATH`, and nothing else. The only edit is that the temp directory the capture
ran in has been shortened to `~/demo`, and `python3 tickets.py` written as
`tickets`, so the lines fit. Output is otherwise verbatim.

Total elapsed: under a minute. Four commands.

---

## 1. `tickets quickstart`

One command takes you from a repo with no board to a board with work on it and
you registered against it.

```console
$ cd ~/demo
$ tickets quickstart --agent alice --roles backend
board: ~/demo/.tickets
  resolved from the git worktree cwd is in (~/demo)
wrote: ~/demo/.cursor/rules/tickets.mdc
wrote: ~/demo/AGENTS.md
wrote: ~/demo/.gitignore
wrote: ~/demo/.tickets/MASTER.md
bound: `tickets` run from ~/demo resolves to this board.

Claude Code picks this up from its global SessionStart hook.
Codex and Cursor read AGENTS.md; Cursor also gets .cursor/rules/tickets.mdc.

board: ~/demo/.tickets
epic:  E-001  Sample epic: a first slice end to end
  T-001  Sample: design the data model              ready now
  T-002  Sample: build the API on top of the model  after T-001
  T-003  Sample: put a screen on the API            after T-002
joined as alice  roles=['backend']  can=-  cost=medium

The three commands that matter:
  TICKET_AGENT=alice tickets next                      claim the next ready ticket
  TICKET_AGENT=alice tickets update <id> "..."         say where you are, at least every 45 min
  TICKET_AGENT=alice tickets review <id> --notes "..." hand it back with evidence

See it: tickets ui        ->  http://127.0.0.1:8765   (read-only, auto-refresh)
Learn it: tickets guide   |   docs/first-session.md   |   README.md
```

Two things worth noticing before moving on.

**`bound:`** is the tool confirming that `tickets` run from this directory
resolves to the board it just wrote. It is a real check, not a pleasantry — an
`init` that writes a board somewhere your later commands will not find is the
failure this line exists to rule out. If they had disagreed, quickstart would
have refused and written nothing.

**The three sample tickets are a chain**, not three loose items: `T-002` is
`after T-001`, `T-003` is `after T-002`. That matters in step 3.

## 2. `tickets next` — the first claim

```console
$ TICKET_AGENT=alice tickets next
[>] IN PROGRESS T-001  Sample: design the data model  (E-001; role=backend; owner=alice)

Read this briefing before editing:
  ~/demo/.tickets/MASTER.md
Epic E-001: Sample epic: a first slice end to end  [            ] 0/3
  Created by `tickets quickstart` so the board is not empty on day one.
  Remove the samples with `tickets quickstart --remove`.
  also in this epic: T-002 [ ]; T-003 [ ]
Waiting on this: T-002 Sample: build the API on top of the mode
Time: active 0m, waited 0m before claim, last update 0m ago

A sample ticket created by `tickets quickstart`.

It has no dependencies, so it is the one `tickets next` hands out first.
Work it like a real ticket: claim it, post an update, then send it to review.
Delete the samples whenever you like: tickets quickstart --remove

RULE: you are on branch 'main' in the primary worktree. Work on your own tree:
  git -C ~/demo worktree add .worktrees/alice-work -b alice-work
  cd ~/demo/.worktrees/alice-work

Post progress with `tickets update T-001 "..."` at least every 45 min; finish with
`tickets done T-001 --notes "branch@sha, paths, decisions"`.
```

`next` did not just hand over an id. It printed the briefing files to read, the
epic and its progress, **what is waiting on this ticket** (`T-002`), and the
rule about working in your own worktree rather than on `main`. That block is
the handoff context that would otherwise live in a chat window.

## 3. `tickets update` — say where you are

```console
$ TICKET_AGENT=alice tickets update T-001 "sketched the schema; two tables so far"
update on T-001 recorded (0m into the task)
```

Every 45 minutes, at least. Silence is treated as a timeout, because from the
outside a thinking agent and a dead one look identical.

## 4. `tickets graph` — see the shape of the work

```console
$ TICKET_AGENT=alice tickets graph
Dependency graph (2 open, 1 claimed)
`- [>] T-001 Sample: design the data model  (backend; @alice)
   `- [ ] T-002 Sample: build the API on top of the model  (backend; waiting on T-001)
      `- [ ] T-003 Sample: put a screen on the API  (console; waiting on T-002)
```

## The thing that surprises people first

Ask for another ticket as a second agent and you get:

```console
$ TICKET_AGENT=bob tickets next
no ticket ready: 2 open, all waiting on unfinished work (in progress with: alice)
```

This is not the board being empty or broken. `T-002` and `T-003` are waiting on
`T-001`, and `T-001` is in progress. The board would rather tell an agent there
is nothing to do than hand it work whose prerequisite does not exist yet —
starting too early is one of the failure modes the whole tool is built around.

Finish `T-001` and `T-002` becomes claimable.

## From here

```sh
tickets review T-001 --notes "paths, tests run, decisions"   # hand it back with evidence
tickets quickstart --remove                                  # delete the samples
tickets ui                                                   # watch it live
tickets guide                                                # connect claude / codex / cursor
```

Then read the worker loop and master loop sections of
[README.md](../README.md), and `tickets connect` for wiring a real agent to the
board.

Taking the master seat in a fresh session:
[onboarding/master-howto.md](onboarding/master-howto.md).
