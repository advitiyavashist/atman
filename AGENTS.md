## Shared ticket board

Work here is coordinated through a ticket board that Claude Code, Codex and
Cursor all share. It lives in `.tickets/` and is driven only through the
Atman CLI -- never edit files in `.tickets/` by hand, or atomic claiming
breaks and two agents will do the same work. The primary public name is `atm`;
`tickets` is a compatibility alias for the same implementation, arguments,
exit codes, and board.

Run `atm board` for the current state, or `atm graph` to see the whole
dependency tree with each node's status and owner.

**Connect first** (once per session; `atm connect` prints the long form):

    export TICKET_AGENT=<your-unique-name>     # claude-opus, codex, grok ...
    atm join $TICKET_AGENT --roles backend --can docker,browser --cost high
    # omit --harness: prints harness=claude (default); label only until watch/spawn
    atm master                             # briefing + health
    atm inbox                              # messages addressed to you

**Accept gate**

`atm review` pins the review head. A DIFFERENT seat must review that exact
head and record `atm accept <id> --sha <full 40-char review head> --notes "evidence"`.
Never self-accept, including when acting as master or CoS. Dependents stay shut
until that accept is recorded and the dependency is complete; a DONE label alone
is not verification. A moved head voids the accept: submit a new review and obtain
a new independent accept at the new head before integration or release.

**Rules for every agent**

1. Claim before you work (`atm next`). Never work without a ticket; never
   edit `.tickets/` by hand. Hold one ticket at a time.
2. Finish what you claim. If you cannot, `atm block --reason` or
   `atm reopen` -- never go silent.
3. You may create, split, re-wire and assign tickets. Extending the graph is
   expected. Anyone can become master with `atm master take`.
4. Work on your own git worktree and branch, never on main. Commit as you go.
   `atm review` refuses from main or with uncommitted files; run `atm sync` first.
5. Post `atm update <id> "..."` at least every 45 minutes and at every
   milestone. Longer silence is treated as a timeout and the ticket may be
   reopened for someone else.
6. When finished, submit -- do not close: `atm review <id> --notes "paths
   touched, tests run, decisions dependents must match"` (branch@sha is added
   automatically; `--pr N` if you opened one). Follow the accept gate above.
   The integrator may merge only the independently accepted review head.
   Claim your next ticket right away.
7. Tickets can declare `needs` (docker, browser, own-machine, gpu ...). You only
   receive tickets whose needs you registered with `--can`. Expensive agents
   are steered to priority-1 work, cheap agents to routine work.
8. Stuck? Say so at once: `atm msg "stuck: <what, tried, need>" --to <master>
   --re <id>` and `atm update <id> "stuck: ..."`; `atm block` if you
   cannot continue. The master's job is to unblock you. Never wait silently.

Statuses: TO DO -> IN PROGRESS -> IN REVIEW -> DONE, or BLOCKED. Set them with
`atm status <id> todo|in-progress|review|blocked|done` or the dedicated
commands below; `atm list` shows the label on every line.

**The loop**

    atm next                          # claims -> IN PROGRESS; prints handoffs + timing
    atm update T-002 "..."            # progress, every 45 min
    atm msg "question" --to claude-opus --re T-002
    atm review T-002 --notes "..."    # -> IN REVIEW; master is messaged
    atm who                           # where everyone is: worktree, branch, ticket

`atm next` prints the ticket body, briefing paths, every note on direct
dependencies, and the latest note on earlier ancestors. Only one agent can
ever hold a ticket.

**Epics and sprints.** Tickets carry `epic` (E-001) and `sprint` (S-01).
`atm next` prefers the active sprint. `atm epic list` / `atm sprint show`
give progress bars; `atm sprint close S-01 --carry S-02`
rolls unfinished work forward.

The `--notes` text on `review`/`accept`/`done` is shown to whoever picks up a
dependent ticket, together with the accepted sha.
Write what the next agent needs -- file paths, names, decisions they must match
-- not a summary of your effort.

**As the planner**, create the whole dependency graph in one shot. `deps` may
reference a `key` from the same plan or an existing `T-` id:

    atm plan <<'EOF'
    [{"key":"api","title":"Build REST API","role":"backend","body":"details","deps":[]},
     {"key":"ui","title":"Build login UI","role":"frontend","deps":["api"]}]
    EOF

Tickets whose dependencies are unfinished stay invisible to `atm next`, and a
finished dependency stays withheld until a DIFFERENT seat accepts its exact sha
(`atm accept <id> --sha <full 40-char review head>`), so nobody -- human or
agent -- can release their own work. `atm quickstart --gate` demonstrates that in a throwaway dir.

**Adding work to a graph that already exists.** Any agent can extend the graph
mid-run -- this is normal, not a last resort:

    atm create "Add rate limiting" --deps T-002
    atm create "DB migration" --blocks T-002
    atm dep T-004 --after T-003
    atm dep T-004 --drop T-003
