# tickets

A file-based ticket board for coordinating many AI coding agents (Claude Code,
Codex, Cursor, Grok, anything with a shell) on one codebase. One Python file,
standard library only, no server.

It exists because a swarm of agents fails in predictable ways: two agents do the
same work, work starts before its prerequisite exists, an agent dies on a usage
limit holding a ticket nobody else can see, handoff context lives in a chat
window that is gone, and the human ends up merging conflicts. Every command
here closes one of those gaps.

## Install

```sh
git clone <this repo> ~/tickets
ln -s ~/tickets/tickets.py ~/.local/bin/tickets     # any dir on your PATH
cd your-project && tickets init                     # AGENTS.md, .cursor rule, MASTER.md, .gitignore
```

Optional, Claude Code: add a `SessionStart` hook so every session sees the
board (see `install.sh`).

## The model

- **Ticket** `T-001`: title, body, `role`, `priority` (1 hard .. 3 routine),
  `deps` (a DAG -- cycles and dangling ids are refused), `epic`, `sprint`,
  `needs` (capabilities such as `docker`, `browser`, `own-machine`), owner,
  notes, timings.
- **Status**: `TO DO -> IN PROGRESS -> IN REVIEW -> DONE`, or `BLOCKED`.
- **Epic** `E-001`, **Sprint** `S-01` (one active; `next` prefers it).
- **Agent**: `TICKET_AGENT=name`; registered with roles, capabilities, cost
  tier, model, and what it is best for.
- **Master**: whoever ran `tickets master take`. Reviews, merges, routes,
  logs. Designed to be replaced mid-flight: `tickets master` prints the whole
  handover.

Storage is `.tickets/` at the repo root: one JSON file per ticket, epic,
sprint and agent; an append-only `messages.jsonl`; `MASTER.md` for context and
the decision log. Claims are atomic (`O_EXCL` lock), so parallel `tickets next`
calls never hand out the same ticket. Git worktrees share the board (it is
resolved through `git rev-parse --git-common-dir`).

## Worker loop

```sh
export TICKET_AGENT=claude-opus
tickets join $TICKET_AGENT --roles backend --cost high --model opus
tickets master                    # briefing: sprint, epics, workforce, review queue, health
tickets inbox                     # messages addressed to you
tickets next                      # atomically claims; prints body, epic, handoff notes from deps, timing
tickets update T-012 "..."        # every 45 min; silence is treated as a timeout
tickets msg "question" --to grok --re T-012
tickets sync                      # merge main into your branch (conflicts are yours, early)
tickets review T-012 --notes "paths, tests run, decisions"   # -> IN REVIEW, branch@sha recorded
tickets next
```

`review` and `done` refuse from `main` or with uncommitted files. `next`
refuses a second ticket while you hold one (`--another` to override).

## Master loop

```sh
tickets master take
tickets master                    # REVIEW QUEUE + HEALTH with fix commands
tickets merge                     # integration worktree -> merge queue branches -> tests -> ff main -> close tickets
git push origin main              # the tool never pushes
tickets route [--claim]           # suggest/assign owners by role, capability, cost, model
tickets limits                    # who is out on a usage limit (records, silence, local tool logs)
tickets master log "why I did X"
```

`merge` auto-resolves conflicts that touch only docs (keeps main's, saves the
branch's copy under `docs/handoffs/conflicts/`), skips branches with code
conflicts and tells you whose `tickets sync` is needed, and never touches main
unless the test command passes (`.tickets/merge.json` to change it).

## Planning

```sh
tickets epic create "Auth" -b "..."
tickets sprint create "Ship auth" --activate
tickets plan <<'EOF'
{"epic":"E-001","sprint":"S-01","tickets":[
 {"key":"db","title":"Schema","role":"backend","priority":1},
 {"key":"api","title":"Auth API","role":"backend","deps":["db"]},
 {"key":"ui","title":"Login UI","role":"console","deps":["api"]},
 {"key":"smoke","title":"Browser smoke","role":"qa","deps":["ui"],"needs":["browser"]}
]}
EOF
tickets create "DB migration" --blocks T-002     # insert a node upstream of existing work
tickets dep T-004 --after T-003                  # rewire
tickets map                                      # sprint -> epic -> tickets, with deps
tickets graph                                    # dependency tree with status per node
```

## Everything else

`board` (compact, used by hooks; silent when there is no board), `list`,
`show`, `who` (where every agent is: worktree, branch, ticket), `here`,
`assign`, `status`, `block`, `reopen`, `note`, `limit`, `connect` (per-tool
onboarding text), `context` (briefing files), `where`, `clear`.

Per-agent briefs: drop `.tickets/briefs/<agent>.md` and it is listed first on
every claim that agent makes.

## Design notes

- No daemon, no database, no network. Works over any shared filesystem.
- Atomicity comes from the filesystem: `O_EXCL` for claims and id allocation,
  `O_APPEND` for messages, rename for updates.
- The tool is deliberately opinionated about process (one ticket at a time,
  updates every 45 minutes, review before done, own worktree) because the
  failure modes it prevents are expensive and the rules are cheap.
- Python 3.9+, stdlib only.

MIT licensed.
