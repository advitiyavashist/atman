# First run

You have a Claude, Codex, or Cursor subscription and a project you want a
small team of agents to work on. This page takes you from nothing to a board
where two agents finish a dependency chain, you record the verdict on each,
you recover a ticket from an agent that died holding it, and the objective
closes with evidence. It works the same for all three subscriptions — you
pick yours once, in step 3.

The command flow was replayed on a throwaway board with an isolated HOME and
install prefix. Provider binaries were replaced with stubs; this verifies
coordination and command wiring, not model quality or your subscription.
Outputs below are illustrative; paths, SHAs, timings and provider text vary.

`atm` is the public command; `tickets` remains a compatibility alias.

---

## 0. Install

One supported install today: clone the repo and run `install.sh`.

```sh
git clone https://github.com/advitiyavashist/atman.git
cd atman
./install.sh
export PATH="$HOME/.local/bin:$PATH"     # if it is not already
```

Expected output:

```
Development install: use --live-release --ref <sha> for the shared live CLI.
linked ~/.local/bin/atm -> <clone>/tickets.py (primary)
linked ~/.local/bin/tickets -> <clone>/tickets.py (compatibility alias)
add ~/.local/bin to your PATH
now: cd <your project> && atm --help   # tickets is the same command
```

(Paths are shown abbreviated: the real output prints absolute paths, and the
proof run for this page used `./install.sh --prefix <scratch>/bin` so as not
to touch the machine's existing `atm`.)

The first line is a mode notice, not an error. `install.sh` refuses to
replace an `atm` or `tickets` it does not own, and exits 1 rather than
clobbering it:

```
install.sh: refusing to overwrite ~/.local/bin/tickets -- it looks like a pinned
live-release launcher (see ./install.sh --live-release). Installing
here would replace the machine's pinned release.
Use --prefix DIR (or PREFIX=DIR) for an isolated install, or --force
to replace it anyway.
```

Take the advice literally: `./install.sh --prefix ~/somewhere` for an
isolated install, `--force` only if you meant to take the existing one over.

Check which file you are actually running before you trust anything else:

```sh
atm self
```

```
script: <clone>/tickets.py
status: tickets (uninstalled checkout; no pinned release)
cli:    primary=atm alias=tickets (one implementation)
PATH:   ~/.local/bin/atm (atm)
        -> <clone>/tickets.py
```

Homebrew, Linux packages, and pipx are **planned, not available**. There is
no published tap or release tarball yet — see
[reference.md](reference.md#install-modes).

---

## 1. Point it at your project

Use a small Git project with a committed `main` branch and a clean working
tree. Python 3 and pytest must already be available to your provider CLI.
Run this in that project, **not** in the `atm` clone.

```sh
cd /path/to/your-project
unset TICKETS_DIR
atm where
```

`atm where` prints the board path it will use. Read it before you go on.
`TICKETS_DIR` overrides board resolution and nothing warns you, so a stale
export from another project silently sends every command below to somebody
else's board.

```sh
atm quickstart --agent boss --roles backend
```

`quickstart` creates `.tickets/`, writes `AGENTS.md` and
`.cursor/rules/tickets.mdc` so Codex and Cursor pick up the protocol, seeds
three sample tickets, and registers you as `boss`. It is safe to run twice.

Look at the samples, then drop them:

```sh
atm quickstart --remove
```

```
removed 3 sample ticket(s) (T-001, T-002, T-003)
epic E-001 left in place (it may hold your own work now)
```

Ticket numbering restarts, so your first real ticket is `T-001` again.

**Commit what quickstart wrote, now.**

```sh
git add AGENTS.md .cursor .gitignore && git commit -m "atm quickstart"
```

`quickstart` leaves those files untracked. If you skip this, the agents you
spawn in step 5 branch from a `main` that does not have them, each writes its
own `.gitignore`, and your first merge aborts with *"untracked working tree
files would be overwritten by merge"*.

---

## 2. Say who you are and what done looks like

```sh
export TICKET_AGENT=boss
atm master take
atm objective "Ship a greet command with a test and a README section" \
  --exit "pytest -q passes and README.md documents greet"
```

```
boss is master now. Run `atm master` for the briefing.
WARNING: boss took master with no live endpoint. Mail stays queued until a
native session or persist watcher is online.
objective set
```

That warning is expected here: no watcher is online for `boss` yet, so mail
to you queues instead of waking anything. It clears once a session or watcher
exists.

`--exit` is not decoration. Leave it off and the objective is still set, but
you get

```
FLAG: no measurable exit criterion; add --exit "<observable end state>"
```

and nothing downstream — not you, not a worker, not the dashboard — can tell
whether the team is finished. It is a warning, not a refusal, so it is easy
to walk past.

---

## 3. Find out which subscriptions you can actually spend

```sh
atm harness available
```

It probes every harness in the catalog, prints a row for each one including
the missing ones, and never creates a seat or spawns anything. Two things to
read per row: `on_disk`, and the usage line.

```
id       name           binaries               on_disk  path / policy
claude   Claude Code    claude                 yes      /opt/homebrew/bin/claude
         ok to spawn if chosen
         if they say yes: atm spawn <seat> --harness claude
         usage unknown remaining=(missing)  reset=(missing)
```

Usage that reads `(missing)` means **unknown**, not exhausted — that CLI does
not report a quota. Installed is not the same as usable: pick the harness you
have a working, logged-in subscription for right now.

Set it once. The rest of this page uses it, so the same commands work whether
you bought Claude, Codex, or Cursor:

```sh
export HARNESS=claude      # or: codex   |   cursor
```

For these fresh seat names, omit `--model` to use the provider CLI default.
An existing seat can retain its registered model; use fresh names for this run.
Model names are provider-specific (`sonnet`/`opus` for Claude, a Codex model name
for `codex`), so a hardcoded one is wrong for two of the three. Add `--model`
later, once you want a specific one.

Each harness resolves to a different command in `tickets.py:_worker_cmd`.
The `cmd:` banner describes it; the watcher can already be starting when you
see the banner. These default command shapes were verified using stub binaries:

| `--harness` | binary it runs |
|---|---|
| `claude` | `claude -p "$(atm prompt)" --dangerously-skip-permissions` |
| `codex` | `codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox "$(atm prompt)"` |
| `cursor` | `agent -p --output-format text --force "$(atm prompt)"` |

If that binary is not installed and logged in, `spawn` refuses before it
starts a watcher, and names the fix:

```
probe-claude: Login required
  recover:  claude auth login
watcher not started; fix the state above, then rerun `atm spawn probe-claude`
```

Antigravity (`agy`) appears in this catalog and is **experimental**. Its
headless mode does not reliably return; do not build your first run on it.

---

## 4. Put a small dependency chain on the board

```sh
atm create "Add greet.py with a pytest test" --role backend \
  --body "Cause: the repo has no greet command. Change: add greet.py and tests/test_greet.py. Proof: pytest -q passes."

atm create "Document greet in README.md" --role docs --deps T-001 \
  --body "Cause: greet.py is undocumented. Change: add a Usage section to README.md. Proof: README.md names greet."

atm graph
```

```
Dependency graph (2 open)
`- [ ] T-001 Add greet.py with a pytest test  (backend)
   `- [ ] T-002 Document greet in README.md  (docs; waiting on T-001)
```

The edge is real, not a sentence in a body. A docs seat asking for work while
T-001 is open gets told so:

```
no ticket for roles ['docs']; 1 ready for other roles: T-001
```

There is a second creation verb, `atm plan`, that takes a JSON graph and is
the right tool once you are planning more than a couple of tickets. It
behaves differently in two ways that will bite you on a first board — read
[master-howto.md](master-howto.md#decision-3--a-graph-not-a-list) before you
use it.

---

## 5. Wake the first agent

```sh
atm spawn dev1 --roles backend --harness "$HARNESS" --every 10 --max-runs 1
```

```
worktree <project>/.worktrees/dev1 (branch dev1)
pinned claude hooks to unique worker dev1 (canonical role hooks not inherited)
watcher for dev1 started (pid NNNNN); harness=claude; model=default;
  wake=task-only; launch=unattended; persist=no; max-runs=1;
  log <project>/.tickets/agents/dev1.watch.log
cmd: claude -p "$(atm prompt)" --dangerously-skip-permissions
```

(The `harness=` and `cmd:` lines follow your `$HARNESS`; the table in step 3
has the other two.)

`spawn` makes the seat its own git worktree and branch, registers it, and
starts a poller. **This spends real model calls** — that `cmd:` line is the
command it will run. `--max-runs 1` stops after one run while you are
watching; `--every 10` polls every ten seconds instead of the default sixty,
which is why a freshly spawned worker otherwise looks dead for a full minute.

Watch it:

```sh
tail -f .tickets/agents/dev1.watch.log
```

```
run 1 trigger={"broadcasts": 3, "ready_in_my_lane": ["T-001 Add greet.py with a pytest test"]}
work found (ready_in_my_lane) wake=ready_in_my_lane ... -> run 1
```

The trigger line names why it woke. When the run ends you see
`run 1 finished exit=0`, `max-runs reached`, `watch stopped`.

```sh
atm list
```

```
[r] IN REVIEW   T-001  Add greet.py with a pytest test  (role=backend; owner=dev1)
[ ] TO DO       T-002  Document greet in README.md  (role=docs; BLOCKED-BY T-001)
```

The agent claimed the ticket, worked in `.worktrees/dev1`, committed, and
submitted the work for review with a reviewable SHA. `atm show T-001` has its
notes, including the branch and SHA to look at.

> **If your project has no `origin` remote,** the worker will report that
> `atm sync` refused with *"no 'origin' remote configured"* and that it used
> `atm review --force`. That is correct on a local-only repo — there is no
> remote to be stale against. On a repo that does have a remote, `--force`
> there is skipping a real check.

---

## 6. Review the artifact yourself and record the verdict

Review is your responsibility in this walkthrough. On this main, readiness
checks the predecessor's `done` status, not an enforced accepted verdict.
Record acceptance against the reviewed SHA, merge, and only then close it.

```sh
cd .worktrees/dev1 && git log --oneline -2 && python3 -m pytest -q ; cd -
```

```
8a3a668 Ignore .claude/ local tool state
f343dde Add greet.py with pytest test
..                                                                   [100%]
2 passed in 0.01s
```

Then say so on the board, against the exact SHA you checked:

```sh
atm accept T-001 --sha $(git -C .worktrees/dev1 rev-parse HEAD) \
  --notes "ran pytest -q in .worktrees/dev1 -> 2 passed; greet('x') -> 'Hello, x!'. Matches the exit criterion."
```

```
T-001 accepted 8a3a6689e2c305bf8e8920ae34976fe0b80f53d7 by boss
```

`accept` records a verdict; it does not close the ticket and does not release
the work that depends on it. Fast-forward main, then close. This keeps the
accepted SHA as the merged artifact; a new merge commit would have a different
SHA and the app would mark the earlier verdict superseded. If fast-forward
refuses, have the worker rebase, resubmit and get its new SHA reviewed first:

```sh
git merge --ff-only dev1
atm done T-001 --notes "greet.py + tests/test_greet.py merged to main. greet(name) -> 'Hello, {name}!'."
```

```
T-001 done in 2m (waited 0m before claim)
unblocked: T-002
```

`atm show T-001` now carries both, separately: your verdict under **Review
events**, the agent's evidence under **Notes**.

---

## 7. Claim the second ticket and interrupt it once

For this bounded recovery exercise, drive the writer seat manually from a
separate worktree. This avoids racing an unattended model that might finish
before you can interrupt it. Run from the project root:

```sh
git worktree add .worktrees/writer -b writer main
cd .worktrees/writer
export TICKET_AGENT=writer
atm join writer --roles docs --harness "$HARNESS"
atm next
atm update T-002 "Checked greet(name): returns Hello, {name}!; README still needs a Usage section. No file changes yet."
cd ../..
export TICKET_AGENT=boss
```

`atm next` claims T-002 because T-001 is done and prints **Handoff from
dependencies**, including dev1's review evidence and the coordinator's merge
note. The progress note deliberately leaves no uncommitted file to recover.
Stop this manual seat here; do not keep working under its identity.

---

## 8. Reopen and replace the interrupted seat

As coordinator, release the claim with a recorded reason (`--notes` is the
actual reopen flag):

```sh
atm reopen T-002 --notes "Bounded interruption: writer stopped after its progress update; replacement should finish the Usage section."
atm spawn writer2 --roles docs --harness "$HARNESS" --every 10 --max-runs 1
atm show T-002
```

The replacement's `atm next` claim prints the prior notes: T-001's dependency
handoff, writer's `Checked greet(name)` progress, and boss's `Bounded
interruption` reason. Check those same notes with `atm show T-002`. Reopening
releases ownership and preserves notes; it does not copy uncommitted files
between worktrees. Wait for writer2 to commit and submit `atm review` before
continuing. Its log is `.tickets/agents/writer2.watch.log`.

---

## 9. Review and close the second ticket

Same gate as step 6 — the replacement seat's SHA, not the dead one's:

```sh
(cd .worktrees/writer2 && python3 -m pytest -q)
cat .worktrees/writer2/README.md
atm accept T-002 --sha $(git -C .worktrees/writer2 rev-parse HEAD) --notes "Tests pass; README Usage section names greet and shows its output."
git merge --ff-only writer2
atm done T-002 --notes "README Usage section merged; greet command and tests are on main."
python3 -m pytest -q
```

---

## 10. Close the objective

An empty board is not a finished one. Say so explicitly, with evidence:

```sh
atm objective --done "greet.py + tests/test_greet.py and README.md Usage section on main; pytest -q -> 2 passed; T-001 and T-002 both accepted against their exact SHAs."
```

```
objective marked achieved
```

```sh
atm objective
```

```
OBJECTIVE -- ACHIEVED / MET (set by boss, 2026-09-15T01:34:47Z)
Ship a greet command with a test and a README section
exit: pytest -q passes and README.md documents greet
evidence: greet.py + tests/test_greet.py and README.md Usage section on main;
  pytest -q -> 2 passed; T-001 and T-002 both accepted against their exact SHAs.

review queue: empty
ready and unowned: none
```

Skip this and the board reads `ACTIVE` with nothing on it forever — which
looks identical to a team that stalled.

---

## 11. Look at what you built

Completed, accepted tickets leave the active Work graph on this main. Post
one closing handoff so their full review records remain visible in the app's
**Intervene → Board** thread:

```sh
atm msg "First-run closing handoff:
$(atm show T-001)

$(atm show T-002)" --to everyone
atm ui
```

```
board UI: <local address>  (Ctrl-C to stop; localhost-only; composer posts via atm msg)
```

Open the local address `atm ui` prints. The app auto-refreshes and binds to localhost;
its composer can post messages. Stop it with Ctrl-C when finished. What should
be visible, and is worth checking against what you just did:

- **Objective:** state `achieved`; the completion message carries the evidence
- **Work:** `2/2 accepted`, with no active work left; completed nodes and their
  edge are omitted from this active graph
- **Intervene → Board:** the closing handoff contains both tickets, the
  T-001 → T-002 dependency result, each **accept** event with its full reviewed
  SHA and reviewer, and the notes writer2 inherited
- the closing handoff also preserves writer's progress and boss's reopen reason
- turns and cost reading **unknown** / `—` when no usage was reported, as in
  the stub replay; a real harness may supply measured usage. Unknown is not zero

`atm ui --json` prints the same snapshot without serving it, which is the
easier thing to grep:

```sh
atm ui --json | python3 -c "import json,sys; print(json.load(sys.stdin)['counts'])"
```

```
{'total': 2, 'done': 2, 'done_unverified': 0, 'accepted': 2}
```

---

## Wake an agent without spending a model call

This bounded wiring check works even after both tickets are done. A directed
`--task` message provides the wake; `/usr/bin/true` exits without a model call.

```sh
atm join probe --roles backend --harness custom --cmd /usr/bin/true
atm msg "Dry wiring check" --to probe --task
atm watch --agent probe --every 1 --max-runs 1 --cwd . --exec /usr/bin/true
```

Expect one run, exit 0, then `max-runs reached` and `watch stopped`. This
proves task delivery and process execution; the stub does not claim or
complete work. Keep `--max-runs 1` so a diagnostic cannot run indefinitely.

---

## Stop everything

```sh
atm spawn --list
atm spawn dev1 --stop
atm spawn writer2 --stop
atm spawn probe --stop
```

A `task-only` watcher stops itself after its run. A `continuous` one does
not; it keeps polling until you stop it, including after you have walked
away.

---

## Where to go next

| You want | Read |
|---|---|
| The decisions the coordinating seat makes | [master-howto.md](master-howto.md) |
| A worker's own loop, claim to reviewable SHA | [../first-session.md](../first-session.md) |
| Standing context per role | [role-context.md](role-context.md) |
| Your own harness, not Claude/Codex/Cursor | [../byoa.md](../byoa.md) |
| Paths, flags, prompt order, footguns | [reference.md](reference.md) |
