# First run: two agents in five minutes

You installed `atm`. You have a Claude, Codex, or Cursor subscription. This
page ends with a board that has an objective, a dependency graph, and a
second agent that wakes on its own and picks up work.

Nothing here spends a model call until the last step, and that step is
opt-in. Every command below was run end to end on a throwaway board before
this page was written; the whole sequence takes about 18 seconds of machine
time, so the five minutes is you reading and answering, not waiting.

`atm` is the command. `tickets` is the same file under its old name — every
`atm <verb>` below works as `tickets <verb>`.

---

## 0. Install

From a clone:

```sh
./install.sh                      # links ~/.local/bin/atm and ~/.local/bin/tickets
atm --version
```

`install.sh` prints `Development install: ...` first. That is a mode notice,
not an error. It refuses to clobber an `atm` it does not own; use
`./install.sh --prefix ~/some/dir` for an isolated install, or `--force` to
take over.

Check which file you actually run before you trust anything else:

```sh
atm self
```

---

## 1. Make a board in your project

Run this in the repo you want coordinated — not in the `atm` clone.

```sh
cd /path/to/your-project
atm quickstart --agent boss --roles backend
```

`quickstart` creates `.tickets/`, writes `AGENTS.md` and
`.cursor/rules/tickets.mdc` so Codex and Cursor pick up the protocol, seeds
three sample tickets, and registers you as `boss`. It is safe to run twice.

Drop the samples once you have looked at them:

```sh
atm quickstart --remove
```

`atm` resolves the board from the git worktree you are standing in. If
`TICKETS_DIR` is exported from some other project, it wins over that and
every command goes to the wrong board. `atm where` prints the board path;
`atm board` prints the path plus what is on it.

---

## 2. Take the coordinating seat and say what done looks like

```sh
export TICKET_AGENT=boss
atm master take
atm objective "Ship GET /hello with a test" --exit "pytest -q passes and the route returns 200"
```

`--exit` is not decoration. Without it `atm objective` answers
`FLAG: no measurable exit criterion`, and nothing downstream can tell you
whether the team is done.

`master take` warns `no live endpoint` if no session or watcher is online for
this seat yet. That is expected here — mail queues until step 5.

---

## 3. Find out which subscriptions you can actually spend

```sh
atm harness available
```

This probes every harness in the catalog and prints a row for each one,
including the ones that are missing. It never creates a seat and never
spawns. Read two things per row: `on_disk`, and the usage line. Usage that
reads `(missing)` means **unknown**, not exhausted — the CLI in question does
not report a quota.

Pick from what you have a working subscription for right now. Installed is
not the same as usable.

---

## 4. Put real work on the board

Two verbs create tickets, and they do **not** behave the same way.

```sh
atm create "Add GET /hello returning JSON" --role backend
```

`atm create` produces a ticket in `lane=ready`. A worker can claim it
immediately. Use this for the first run.

For work with real dependencies, use `atm plan` so the edges are actual
`--after` links rather than a sentence in a ticket body. Give every ticket a
`cause`, a `change`, and a `proof`:

```sh
atm plan <<'EOF'
[{"key":"api","title":"Add GET /hello returning JSON","role":"backend","deps":[],
  "cause":"no endpoint","change":"add the route","proof":"pytest -q"},
 {"key":"doc","title":"Document the hello endpoint","role":"docs","deps":["api"],
  "cause":"api landed","change":"add a README section","proof":"section exists"}]
EOF
atm graph
```

Those three fields are load-bearing, not commentary. **With all three, each
ticket lands in `lane=ready` and a worker can claim it. Leave any of them out
and the ticket lands in `lane=capture`, where `atm next` will not hand it
out** — you get a plan that staffs nobody, and a spawned worker that
correctly reports there is nothing to do.

Most published `plan` examples, including older ones in this repo, omit the
three fields. That is the single most likely way to lose ten minutes on your
first board.

If you already planned without them, move each ticket across by hand:

```sh
atm sound T-001 --notes "cause=no endpoint; change=add GET /hello; proof=pytest -q; deps=none"
```

`atm next` tells you when you are in this state —
`T-001 waits in capture: run tickets sound T-001` — so it is recoverable, not
fatal.

---

## 5. Wake a second agent

A second agent is a seat plus something that runs when the board has work for
it. Two ways in, depending on whether the agent is interactive.

**Interactive Claude Code**, in the worktree that agent will work in:

```sh
atm hooks claude --agent dev1
claude
```

That writes `.claude/settings.json` hooks: SessionStart pulls the board,
UserPromptSubmit pulls the inbox, Stop keeps the session working while board
work remains. The identity is baked into the hook — the launching shell's
`TICKET_AGENT` is ignored, which is what you want when several seats share a
machine.

**Unattended**, with its own git worktree and a detached watcher:

```sh
atm spawn writer --roles docs --harness claude
atm spawn --list
```

`spawn` creates `.worktrees/writer` on branch `writer`, registers the seat,
and starts a poller. Confirm it is alive before you walk away:

```sh
tail -f .tickets/agents/writer.watch.log
```

The log stays empty until the first poll. **The default interval is 60
seconds**, so a freshly spawned worker can look dead for a full minute. Pass
`--every 10` while you are watching it for the first time.

When it fires, the log names what woke it:

```
run 1 trigger={"broadcasts": 1, "ready_in_my_lane": ["T-002 Document the hello endpoint"]}
```

Stop it with `atm spawn writer --stop`.

### Prove the wake without spending a model call

Before you point a paid subscription at this, run the same machinery through
a shell script. `atm` only cares about the exit code, and it expands
`{prompt_file}`, `{cwd}` and `{agent}`:

```sh
cat > /tmp/dry.sh <<'EOF'
#!/bin/sh
echo "woke: agent=$3 cwd=$2 prompt_bytes=$(wc -c < "$1")"
cat "$1" > /tmp/captured-prompt.txt
EOF
chmod +x /tmp/dry.sh

atm spawn writer --roles docs --every 10 --max-runs 1 \
  --exec '/tmp/dry.sh {prompt_file} {cwd} {agent}'
```

Then read `/tmp/captured-prompt.txt`. That file is exactly what a real
harness would have received: the seat's rules, the board path, the loop, and
any role context you seeded. If it looks right, swap `--exec` for
`--harness claude` and you are running the real thing.

The prompt file is written fresh per run and deleted after. Do not cache its
path.

---

## 6. Check the two seats can see each other

```sh
atm who
atm msg "T-001 lands as GET /hello -> {\"ok\":true}" --to writer --re T-001
```

As `writer`, `atm inbox` shows that message. That is the whole collaboration
primitive: agents talk on the board, not through you.

`atm who` reports `state unknown ?` and `reachable=no` for seats it cannot
find a live transcript for. On a fresh board that is every seat, including
one that just ran successfully. It is a transcript-detection gap, not a
broken board — trust the watch log over this column.

---

## You now have

- a board bound to your project, with an objective and an exit criterion
- a dependency graph with real `--after` edges
- a coordinating seat
- a second agent that wakes on its own, in its own worktree, with injected
  context

## What to read next

- Working a ticket to a **reviewable SHA**, where **human review** is the
  gate: [../first-session.md](../first-session.md)
- The decisions the coordinating seat makes: [master-howto.md](master-howto.md)
- Standing context per lane: [role-context.md](role-context.md)
- Your own harness: [../byoa.md](../byoa.md)
- Paths, flags, prompt order, footguns: [reference.md](reference.md)

## Known rough edges on a first run

Real, reproduced on a clean install. None of them block you; all of them
cost time if nobody warns you first.

1. `atm plan` silently produces an unclaimable board when the plan JSON
   omits `cause`/`change`/`proof` — and the examples shipped in this repo
   omitted them.
2. Bare `atm` prints seventy subcommands and no starting point.
3. `atm guide` exits 1 on a repo with no board and tells you to "create a
   ticket first" instead of naming `atm quickstart`.
4. `atm --version` and most command output still say `tickets`.
5. `atm spawn` defaults to a 60-second first poll with no output.
6. `atm assign --owner` hands a seat a second in-progress ticket without
   complaining, though every prompt says one ticket at a time.
7. `atm who` shows `reachable=no` for healthy seats.
