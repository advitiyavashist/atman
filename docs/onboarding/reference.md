# Onboarding reference

Everything a first read does not need. Come here when a path, a flag, or a
surprise sends you looking. The first read is
[first-run.md](first-run.md); the coordinating seat's decisions are in
[master-howto.md](master-howto.md).

`atm` and `tickets` are the same file. Either name works everywhere.

---

## Install modes

**Supported today: a clone on PATH.** Everything else below is either a
development mode or not shipped yet.

**A. Clone on PATH (the supported path)**

```sh
git clone https://github.com/advitiyavashist/atman.git
cd atman
./install.sh                       # links atm + tickets into ~/.local/bin
./install.sh --prefix ~/isolated   # or somewhere of your own
atm --version
# a raw checkout answers: tickets (uninstalled checkout; no pinned release)
```

`install.sh` refuses to replace an existing `atm` or `tickets` that is not
already a symlink to this checkout — including a pinned release launcher.
`--force` overrides.

**B. Pinned live-release snapshot (shared CLI, same clone)**

```sh
./install.sh --live-release --ref <commit-sha>
atm --version
# tickets commit <sha> (verified release)
```

This writes a launcher that `exec`s a frozen snapshot under
`tickets-releases/<sha>/`. Editing the clone does **not** change that binary
until you install a new ref.

**C. Homebrew (published at v0.3.0)**

```sh
brew tap advitiyavashist/tap
brew install atman
```

Pin `sha256` to the downloaded GitHub Release asset, not a local build
(`packaging/homebrew/README.md`). The in-repo `packaging/homebrew/atman.rb`
is a copy of the tap formula.

**Not available yet.** Do not plan around these:

| Path | Status as of 2026-09-19 |
|---|---|
| Linux packages | **Planned.** Tested on macOS only. |
| pipx / PyPI | **Planned.** Nothing is published. |

**Confirm before you trust any command**

```sh
atm self          # script path and install kind
which -a atm
atm --version
```

If `--version` says `verified release` and you thought you were running the
clone, you are on the launcher. If it says `uninstalled checkout`, you are on
whatever file the symlink points at — including a dirty working tree. `atm
self` prints the resolved target, which is the only answer that settles it
when several checkouts are on one machine.

`--version` also prints `source:` (the file that is running) and, for a git
checkout, `source-sha:`. If that sha is behind `origin/main` it warns and
prints the refresh command. An operator worktree such as
`atman-runtime-current` that is not updated after merge will omit new
commands from `atm --help` (T-1080). Refresh it with the printed line, or:

```sh
git -C <runtime-worktree> fetch origin
git -C <runtime-worktree> merge --ff-only origin/main
atm --version    # source-sha should match origin/main
```

`atm init` and `atm quickstart` belong in the project you are coordinating,
never inside the `atm` clone.

---

## Board resolution

`atm` resolves `.tickets/` from the git worktree the cwd is in. Linked
worktrees of one repo share the main worktree's board, deliberately; do not
"fix" that by creating a second `.tickets/` in a worktree.

`$TICKETS_DIR` overrides everything. A stale export from another project
sends every command to the wrong board and nothing warns you. `atm board`
prints the resolved path. Details: [../board-resolution.md](../board-resolution.md).

---

## Seats

| Seat | How you create it | Prompt it gets | Wakes on |
|---|---|---|---|
| Worker | `join` / `spawn` | worker loop: inbox → mine → next → review | DMs, held ticket, ready work in its roles |
| Master | `master take`, or `spawn --master` / `drive` | unblock, review + merge, coordinate. No feature tickets | review queue, `stuck:` / `blocked:`, CRIT health, heartbeat if set |
| Master planner | master while a chief of staff is set | scope, route by cost/model, staffing. Does not merge unless the CoS is silent | same as master, plus vision drift |
| Chief of staff | `master cos <name>` or `spawn --cos` | review, unblock, merge. Escalates scope to the planner | same pending keys as master |

`atm drive "<objective>" --as boss --heartbeat 30` sets the objective and
spawns the master seat with a heartbeat in one step. Only seats with a
heartbeat wake when the board is quiet.

`--wake-mode task-only|continuous|scheduled` is a durable seat policy, not a
model or harness choice. Master and CoS seats default to `continuous`; other
seats default to `task-only`. A continuous adapter stays alive and treats
direct messages and named mentions as task wakes. `scheduled` stays alive for
explicit tasks and assignments — the mode itself does not create a clock;
configure a heartbeat or an external cadence separately.

`spawn` with no `--harness` uses whatever `join` registered. Passing
`--harness` overrides **and** re-registers, and a harness switch without a
new `--cmd` drops the old command template on purpose.

**Remote sessions are experimental and outside the supported preview.**
Remote control and reconnect are unfinished; use a local Claude, Codex or
Cursor CLI seat for this onboarding path.

---

## Prompt inject order

One renderer. Built-in CLIs and `{prompt_file}` harnesses get the same text.

**Every seat, first:**

1. `.tickets/briefs/_shared.md`, if non-empty
2. `.tickets/briefs/roles/<role>.md` for each role in that agent's
   `roles.json`, in listed order — skipping `_shared`, duplicates, and
   missing files
3. A relevant, deduplicated, bounded subgraph of repo-backed `knowledge/`

**Then, workers only** (`atm prompt`, or watch/spawn without `--master` /
`--cos`):

4. Standing brief `.tickets/briefs/<agent>.md`
5. Heartbeat / standing-seat block, if `drive_every` is set and an objective
   is open
6. Ticket context notes on held tickets
7. Any extra text the caller passed

**Then, master / planner / CoS** (`atm prompt --master` / `--cos`, or
`spawn --master` / `--cos`):

3. Drive block — objective, status, and "advance the plan or log why not" —
   if an objective is open
4. Master or planner prompt (planner if a CoS exists and this seat is not it)
5. The CoS prompt is the master prompt rewritten for review, unblock, merge

Role files and agent briefs cap at 6000 characters each, then
`...(role context truncated; read the file)`.

`atm master take` records who holds the seat. It does **not** change what
`atm prompt` prints. Unattended master wakes must use `spawn --master` or
`atm prompt --master`.

**Path lock:** inject reads only the paths above. Repo-root `roles/` and
`$TICKETS_ROLES_DIR` are not sources — `roles/` holds human templates. Copy
ideas from them; do not expect watch or spawn to load them.

---

## Folder map — tracked vs local

Default `atm init` gitignores all of `.tickets/`. For a board you intend to
hand over, track the standing files and ignore the live state.

| Path | What | Typical git |
|---|---|---|
| `.tickets/T-*.json` | one file per ticket | local |
| `.tickets/epics/`, `sprints/` | epic / sprint records | local |
| `.tickets/agents/` | check-in, watch pid/log, harness check | local |
| `.tickets/roles.json`, `workforce.json` | who exists, roles, harness, cost | local |
| `.tickets/messages.jsonl` | append-only comms | local |
| `.tickets/objective.json` | standing objective | local |
| `.tickets/trajectories.jsonl` | turns / cost events (no prompt text) | local |
| `.tickets/merge.json` | optional merge test command | local, or tracked if you care |
| `.tickets/MASTER.md` | mission, workforce, current state, decision log | **track** |
| `.tickets/MASTER.decisions.archive.md` | overflow log | track if present |
| `.tickets/CONTEXT.md` | optional claim-time briefing | track if you use it |
| `.tickets/briefs/_shared.md` | every-seat context | **track** |
| `.tickets/briefs/roles/<role>.md` | lane context | **track** |
| `.tickets/briefs/<agent>.md` | per-agent standing brief | **track** |
| `.tickets/AGENT-HANDLES.md` | spawn / resume ids | **track** |
| `.tickets/AUTOMATION-BUDGET.md` | spend cap notes | track if present |
| `HANDOFF.md` (repo root) | what is true about the work | **track** |
| `docs/handoffs/AGENT_CONTEXT.md` | extra claim briefing, if used | track if present |
| `roles/*.md` (repo root) | templates only; not injected | tracked in this repo |
| `knowledge/` | durable facts, evidence, runbooks, skills | **track** |
| `.worktrees/<agent>/` | that agent's git worktree and branch | local |
| `AGENTS.md`, `.cursor/rules/tickets.mdc` | protocol for Codex and Cursor | track |

The split that makes a board handoverable:

```gitignore
.tickets/*
!.tickets/MASTER.md
!.tickets/AGENT-HANDLES.md
!.tickets/AUTOMATION-BUDGET.md
!.tickets/briefs/
```

`atm init --track` commits the **whole** board — locks, ticket JSON,
identities. That is the other extreme. Prefer the split above.

`atm context` prints MASTER.md, CONTEXT.md, and
`docs/handoffs/AGENT_CONTEXT.md` when those files exist.

---

## Footguns

**A sounded ticket cannot reach review without a PR.** `atm review` refuses
any ticket with a `sounded_at` stamp unless you pass `--pr N`, and `--force`
does **not** bypass it:

```
review: sounded code tickets require --pr N (or role=docs|pm and body says "no PR")
```

`atm plan` and `atm sound` both stamp `sounded_at`; `atm create` does not. So
on a project with no GitHub PR to point at, work created by `plan` can be
claimed and finished but never handed back — an unattended worker will do the
job, fail this check, and go stuck. The only exits are a real `--pr`, or
`role=docs`/`role=pm` with the literal words `no PR` in the ticket body. On a
first local board, create tickets with `atm create`.

**`atm sync` refuses without an `origin` remote.**

```
no 'origin' remote configured; cannot confirm main is not stale without one
(comparing against the local main ref is the bug this refusal exists to avoid)
```

The standing worker rule is sync-before-review, so on a local-only repo every
worker hits this and has to submit with `atm review --force`. That is correct
there — there is no remote to be stale against — but on a repo that *does*
have a remote, `--force` at that point is skipping a real check.

**`atm quickstart` leaves its own files untracked.** `AGENTS.md`,
`.gitignore` and `.cursor/rules/tickets.mdc` are written but not committed.
Spawned worktrees branch from `main` without them, each agent writes its own
`.gitignore`, and the first `git merge <agent-branch>` aborts on
*"untracked working tree files would be overwritten by merge"*. Commit them
before you spawn anyone.

**A `plan` without `cause`/`change`/`proof` is unclaimable.** Each ticket in
the plan JSON needs all three fields to land in `lane=ready`; miss any one
and it lands in `lane=capture`, which `atm next` skips. Recover with
`atm sound <id> --notes "cause=…; change=…; proof=…; deps=…"`. `atm create`
goes straight to `ready` regardless. This is the most common first-board dead
end, and it was shipped into this repo's own examples — the two `plan`
snippets in [ceo-mac-runbook.md](ceo-mac-runbook.md) omitted all three until
2026-09-15.

**A default `.gitignore` of `.tickets/` beats a negation.** `atm init`
without `--track` appends a trailing-slash ignore. Git does not descend into
an ignored directory, so `!.tickets/MASTER.md` does nothing on its own. Use
the `.tickets/*` pattern above.

**Never overwrite MASTER.md without reading it.** Append with
`atm master log`. An untracked MASTER.md has no undo.

**Role mismatch.** `atm quickstart` seeds `role=backend` sample tickets and
registers you `--roles backend`. A seat joined `--roles docs` will not be
handed those tickets and will not receive
`.tickets/briefs/roles/backend.md`. `route` and `next` follow `roles.json`
from `join`/`spawn`, not the ticket title.

**Empty role brief honesty.** No file means no role-context block. Inject
does not fall back to repo-root `roles/`. If you wanted house rules in every
backend wake, you forgot to seed `.tickets/briefs/roles/backend.md`.

**`atm accept` does not close a ticket.** It records a verdict under
**Review events** and leaves the ticket in review, so dependent work stays
blocked until the coordinating seat also runs `atm done`. `atm done` stamps
whatever tree it is run from — run it after merging, from the merged tree, or
it records the coordinator's unmerged `main@<sha>` instead of the reviewable
SHA.

**Antigravity (`agy`) is experimental.** It appears in `atm harness
available` because the binary is on disk. Its headless mode does not reliably
return, so it is not a harness to build a first board on.

**Two seats, one worktree.** `spawn` gives each seat `.worktrees/<name>`. Do
not point two live seats at the same tree.

**Paid smoke.** `harness check` and `watch` run the real command; a Claude or
Codex check burns a real call. Use the dry `--exec` harness in
[first-run.md](first-run.md) first.

**A continuous watcher re-runs the same ticket.** If the harness wakes but
never claims, `--wake-mode continuous` will keep firing on the same ready
ticket every interval. A dry `--exec` that only prints will loop; cap it with
`--max-runs` while testing.

**`atm prompt` is the worker prompt.** `master take` does not flip it. Use
`--master` or `--cos` to preview those seats. A watcher spawned without
`--master` runs the worker loop even if that agent currently holds master.

**`review` refuses from `main` and refuses a dirty tree.** Work on the
agent's own branch; commit before submitting. `--force` overrides both. `done`
applies the same two rules — except on a ticket already in `review`, which the
coordinating seat is expected to close from `main` after merging, so those two
checks are waived there. Notes are required either way.

**`atm merge` never pushes.** It fast-forwards local main after tests. You
run `git push`.

---

## What Atman does not do

- **Persist conversation.** Briefs are operator-written standing
  instructions; the knowledge graph is reviewed evidence. Neither stores
  chat, raw prompts, or tool traces.
- **Drive the model's inner loop.** It hands over a prompt file and a cwd.
  Reasoning, tools, retries, and stopping belong to the harness.
- **Parse harness stdout.** Only the exit code, for backoff and
  `harness check`. Everything the board knows, an agent wrote with `atm`.
- **Manage credentials.** API keys and `/login` are yours. `atm limit` is how
  you tell the board someone is out.
- **Sandbox anything.** `--safe` only changes built-in CLI permission flags.
- **Hook a custom harness.** Hooks are Claude, Codex, and Cursor. Custom
  agents get their context from the prompt file.

---

## Commands you will actually type

```sh
atm master                  # briefing
atm master take             # become it
atm master log "…"          # decision log
atm inbox                   # messages to you
atm plan                    # JSON keys + deps -> real --after edges
                            # needs cause/change/proof per ticket to land ready
atm sound T-001 --notes "…" # capture -> ready
atm dep T-004 --after T-003
atm map                     # sprint -> epic -> tickets
atm graph                   # dependency tree
atm update T-002 "…"        # follow-up, at least every 45 min
atm here                    # still alive
atm reopen T-002            # silent claims
atm drive                   # toward the objective
atm dash --once             # status picture
atm route [--claim]         # suggest or assign owners
atm limits                  # who is out, and whether it is auth or a wait
atm merge                   # integration worktree -> tests -> fast-forward main
atm spawn --list
atm spawn <name> --stop
atm brief --role docs --show
atm knowledge query "…"
atm prompt --master --agent boss
atm ui                      # prints its local address; composer can post messages
```

On the dashboard, `—` means **unknown** — not measured yet. It is not zero.
Median turns and yield-at-cost stay `—` until a done ticket reports.

Verbs people miss (`pulse`, `reopen`, `handover`):
[../cli-gotchas.md](../cli-gotchas.md). Trajectory events:
[../trajectories.md](../trajectories.md). Rules to paste into `_shared.md`:
[../agent-operating-rules.md](../agent-operating-rules.md).
