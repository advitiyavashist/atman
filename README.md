# tickets

Atman is a runtime for teams of AI coding agents: bring the agents you already
use — Claude Code, Codex, Cursor, a local model, your own harness — and it owns
the objective, the shared state, the task graph, the messaging, the scheduling
and the verification. `tickets` is its control plane: one Python file, standard
library only, no server, no database, no network.

It exists because a swarm of agents fails in predictable ways: two agents do the
same work, work starts before its prerequisite exists, an agent dies on a usage
limit still holding a ticket nobody else can see, handoff context lives in a chat
window that is now gone, and the human ends up merging the conflicts. Every
command here closes one of those gaps.

## Install

```sh
git clone <this repo> ~/tickets
ln -s ~/tickets/tickets.py ~/.local/bin/tickets     # any dir on your PATH
cd your-project && tickets quickstart               # board + sample work + you, registered
```

Optional, Claude Code: add a `SessionStart` hook so every session sees the
board (see `install.sh`).

## Quickstart

`tickets quickstart` is the whole first run. It is non-interactive and safe to
run twice: it creates the board if there is none, adds a sample epic and three
tickets in a real dependency chain, registers you as an agent, and prints the
three commands that matter.

```sh
cd your-project
tickets quickstart --agent alice        # or: export TICKET_AGENT=alice first
tickets next                            # claims the first sample ticket
tickets update T-001 "poking at it"
tickets review T-001 --notes "what I did, what I ran, what I decided"
tickets quickstart --remove             # delete the samples when you are done
```

Ask for the second ticket and you will be told `no ticket ready`. That is the
dependency graph working, not the board being broken — `T-002` is waiting on
`T-001`. Run `tickets graph` to see the chain.

Watch it live with `tickets ui` (read-only, auto-refreshing, on
<http://127.0.0.1:8765>), and read
[docs/first-session.md](docs/first-session.md) for a real captured first
session, command by command.

## The model

- **Ticket** `T-001`: title, body, `role`, `priority` (1 hard .. 3 routine),
  `deps` (a DAG — cycles and dangling ids are refused), `epic`, `sprint`,
  `needs` (capabilities such as `docker`, `browser`, `own-machine`), owner,
  notes, timings.
- **Status**: `TO DO -> IN PROGRESS -> IN REVIEW -> DONE`, or `BLOCKED`.
- **Epic** `E-001`, **Sprint** `S-01` (one active; `next` prefers it).
- **Agent**: `TICKET_AGENT=name`; registered with roles, capabilities, cost
  tier, model, and what it is best for.
- **Master**: whoever ran `tickets master take`. Reviews, merges, routes, logs.
  Designed to be replaced mid-flight: `tickets master` prints the whole
  handover.

Claims are atomic (`O_EXCL` lock), so parallel `tickets next` calls never hand
out the same ticket. Git worktrees share one board.

## Worker loop

```sh
export TICKET_AGENT=claude-opus
tickets join $TICKET_AGENT --roles backend --cost high --model opus
tickets master                    # briefing: sprint, epics, workforce, review queue, health
tickets inbox                     # messages addressed to you
tickets next                      # atomically claims; prints body, epic, handoff notes from deps
tickets update T-012 "..."        # every 45 min; silence is treated as a timeout
tickets msg "question" --to grok --re T-012
tickets sync                      # merge main into your branch (conflicts are yours, early)
tickets review T-012 --notes "paths, tests run, decisions"   # -> IN REVIEW, branch@sha recorded
tickets next
```

`review` and `done` refuse from `main` or with uncommitted files. `next` refuses
a second ticket while you hold one (`--another` to override).

## Master loop

```sh
tickets master take
tickets master                    # REVIEW QUEUE + HEALTH with fix commands
tickets merge                     # integration worktree -> queue branches -> tests -> ff main -> close
git push origin main              # the tool never pushes
tickets route [--claim]           # suggest/assign owners by role, capability, cost, model
tickets limits                    # who is out on a usage limit
tickets master log "why I did X"
```

`merge` auto-resolves conflicts that touch only docs (keeps main's, saves the
branch's copy under `docs/handoffs/conflicts/`), skips branches with code
conflicts and tells you whose `tickets sync` is needed, and never touches main
unless the test command passes (`.tickets/merge.json` to change it).

## Bring your own agent

Any harness that can run a shell command can be a worker: it just shells out to
`tickets`, so they all share one board.

```sh
tickets connect                   # per-tool onboarding text
tickets hooks claude              # Stop hook: keep a turn alive while board work remains
tickets pending --agent alice     # exit 0 if there is work: unread DM, held ticket, ready ticket
tickets prompt --agent alice      # the standard worker prompt for a headless run
tickets watch --agent alice --every 60 --cwd .worktrees/alice \
  --exec 'claude -p "$(tickets prompt)" --permission-mode acceptEdits'
```

`--exec` takes any tool — `codex`, `cursor-agent`, a script of your own.
`watch --once` is the cron/launchd form. A session cannot be woken by a hook
after its turn ends, which is why waking is a poll plus an `--exec`, not a
callback.

## Driving an objective

Workers wake when there is work for them. The master is different: it has to
keep planning when nothing is pending, or the board goes quiet with the goal
unmet. So the master seat has a heartbeat.

```sh
tickets objective "V1: offline gates green on main, deploy waits on credentials"
tickets drive "<objective>" --as boss --heartbeat 30 --tool cursor+claude
tickets objective                       # objective + drive status
tickets objective --done "evidence"     # closes it; the heartbeat stops
```

Every heartbeat the master prompt carries the objective and a status picture,
and the rule that a heartbeat run ends by advancing the plan or logging why
nothing changed. Only the master seat is driven; the Claude Stop hook ignores
heartbeats, so an interactive session is never pinned open by one.

## Planning

```sh
tickets epic create "Auth" -b "..."
tickets sprint create "Ship auth" --activate
tickets plan <<'EOF'
{"epic":"E-001","sprint":"S-01","tickets":[
 {"key":"db","title":"Schema","role":"backend","priority":1},
 {"key":"api","title":"Auth API","role":"backend","deps":["db"]},
 {"key":"ui","title":"Login UI","role":"console","deps":["api"]}
]}
EOF
tickets create "DB migration" --blocks T-002     # insert a node upstream of existing work
tickets dep T-004 --after T-003                  # rewire
tickets map                                      # sprint -> epic -> tickets, with deps
tickets graph                                    # dependency tree with status per node
```

## Where the data lives

Everything is `.tickets/` at the repo root — plain files you can read, diff and
commit:

| path | what |
| --- | --- |
| `T-001.json` | one file per ticket, so parallel agents never fight over one file |
| `epics/`, `sprints/` | one file each |
| `agents/`, `roles.json`, `workforce.json` | who exists, what they can do |
| `messages.jsonl` | append-only message log |
| `MASTER.md` | master context and the decision log |
| `briefs/<agent>.md` | per-agent brief, shown first on every claim that agent makes |

`tickets init` gitignores the board by default (`--track` to commit it
instead). Board location resolves in this order: `$TICKETS_DIR`, the nearest
ancestor with a live board, the git worktree root, then cwd — see
[docs/board-resolution.md](docs/board-resolution.md).

## Everything else

`board` (compact, used by hooks; silent when there is no board), `list`, `show`,
`who` (where every agent is: worktree, branch, ticket), `here`, `assign`,
`status`, `block`, `reopen`, `note`, `limit`, `context`, `where`, `guide`.

## Design notes

Why it is built this way — no daemon, filesystem atomicity, and why the process
rules are deliberately opinionated — is in
[docs/design-notes.md](docs/design-notes.md).

Python 3.9+, stdlib only. MIT licensed.

Before publishing this repository, see [docs/PUBLIC_PREP.md](docs/PUBLIC_PREP.md).
