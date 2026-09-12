# Master how-to

**You are onboarding.** Say that first. This is not a ticket claim and not a
merge pass.

A new board is set up in this order: **name → integrations → announce that
name on the board → ask for tasks and the objective**. Probe every catalog
row with `tickets harness available` (missing is a row). It auto-checks
usage; missing remaining or reset is a FAIL row. Ask which
integrations to use; do not spawn until they answer. Codex stays in the
catalog even with zero usage. Do not spawn Gemini. No new Claude fable.

After they pick a name and integrations:

```
tickets msg --to everyone "<name> is onboarding. Integrating: <list>. Objective and tasks next. @everyone"
tickets master log "onboarding: name=<name> integrations=<list>"
tickets objective "<their sentence>"
tickets plan <<'EOF'
[{"key":"api","title":"Build REST API","role":"backend","deps":[]},
 {"key":"ui","title":"Build login UI","role":"frontend","deps":["api"]}]
EOF
tickets graph
tickets map
```

Do **not** run one `tickets create` per title. Edges must be real `deps` /
`--after` links. Mid-run: `tickets dep` / `tickets create --blocks`.
Follow-up (master or CoS): `tickets update` / `here`, reopen silent >90m
claims, `tickets drive` toward the objective, review queue. Prose-only
blockers in a ticket body are not edges.

Capture then sound, then CoS dispatch (`tickets capture` / `tickets sound` /
`tickets dispatch --harness …`). CEO does not `tickets next`. The living
board is the index; there is no `plans/` folder tree.

CoS onboarding is the same catalog, then the same graph + follow-up loop
(`tickets master cos <name>`). Do not dump a live-board plan. Auto-start of
children after done is a separate success-trigger, not this step.

You are opening a fresh Claude Code, Cursor, or Codex session that will run
the board as **master**. This is the single read. After it you should be able
to install, take the seat, seed role context, set an objective, and wake
workers without guessing folders.

Atman is a team runtime. You bring the agents you already use. The board
coordinates them. It is **not** a shared-memory brain, a multi-agent SDK, or a
model router.

---

## TLDR

1. Put `tickets` on `PATH` (clone symlink **or** a pinned live-release shim —
   know which).
2. In the **project** repo (not the tickets clone): `tickets quickstart` or
   `tickets init`, then `tickets join` + `tickets master take`.
3. Fill `.tickets/MASTER.md`. Seed `.tickets/briefs/_shared.md` and
   `.tickets/briefs/roles/<role>.md`. Set `tickets objective`.
4. Smoke with a **custom dry harness** (no paid model). Then `tickets spawn`
   / `tickets watch` real workers.
5. Role context comes from `.tickets/briefs/`; repo-root `roles/` is a
   **template only**. Separately, the prompt renderer selects a bounded,
   task-relevant subgraph from repo-backed `knowledge/`. See
   [shared knowledge](../knowledge/README.md).
6. Open `tickets ui` → <http://127.0.0.1:8765>. On the board, **`—` means
   unknown** (not measured yet) — it is not zero. Median turns / yield@cost
   stay `—` until a done ticket reports.
7. **Sound before staff.** `tickets capture` dumps a thought (`lane=capture`,
   invisible to `tickets next`). `tickets sound` is the high-reasoning write
   (cause, change, proof commands, real `--after` deps, no open questions).
   CoS `tickets dispatch` one ready ticket per Cursor seat. `tickets pr-sync`
   after `tickets review --pr`. Master still `tickets done`. CEO does not
   `tickets next`.

---

## Folder checklist (before first wake)

Do this in the project that will hold the board. Tick every line.

- [ ] `which -a tickets` and `tickets --version` — you know which binary
      will run (clone vs release shim).
- [ ] `tickets` from this directory resolves to **this** project's
      `.tickets/` (`tickets board` prints it; or `export TICKETS_DIR=…`).
- [ ] `.tickets/MASTER.md` exists and is **filled in** (Mission is not the
      template placeholder).
- [ ] `.tickets/briefs/_shared.md` exists (house rules every seat sees).
- [ ] `.tickets/briefs/roles/<role>.md` exists for every lane you will
      staff (`backend`, `docs`, …). Missing file = that lane has no
      standing context. That is honest, not a fallback to repo-root
      `roles/`.
- [ ] `TICKET_AGENT` is a real unique name (not `agent-…`).
- [ ] `HANDOFF.md` at the repo root if you are taking over existing work.
- [ ] Optional: `.tickets/CONTEXT.md` if you want a claim-time briefing
      besides MASTER.md.
- [ ] Objective set: `tickets objective` prints a sentence, not
      `no objective set`.
- [ ] Paid CLIs (`claude`, `codex`, `cursor-agent`) are logged in **only**
      if you intend to burn them. First smoke uses a custom dry harness.

---

## Step 1 — Install / which `tickets`

Two delivery modes. Do not mix them on one machine without knowing which
`PATH` hits first.

**A. Clone on PATH (dev / this checkout)**

```sh
git clone <this-repo> ~/tickets
chmod +x ~/tickets/tickets.py
mkdir -p ~/.local/bin
ln -sf ~/tickets/tickets.py ~/.local/bin/tickets
# add ~/.local/bin to PATH if `which tickets` is empty
tickets --version
# expected from a raw checkout:
#   tickets (uninstalled checkout; no pinned release)
```

Same effect: `./install.sh` from the clone (development install).

**B. Pinned live-release shim (shared CLI)**

```sh
# from the clone
./install.sh --live-release --ref <commit-sha>
tickets --version
# expected:
#   tickets commit <sha> (verified release)
```

The live installer writes a **launcher** at `~/.local/bin/tickets` that
`exec`s a snapshot under `tickets-releases/<sha>/`. Editing the clone does
**not** change that binary until you install a new ref.

**Confirm before you trust any command**

```sh
which -a tickets
readlink -f "$(which tickets)"   # GNU; on macOS: realpath or ls -l
tickets --version
```

If `--version` says `verified release` but you thought you were running the
clone, you are on the shim. If it says `uninstalled checkout`, you are on
whatever file the symlink points at — including a dirty working tree.

`tickets init` / `tickets quickstart` belong in the **project** you are
coordinating, not inside the tickets clone.

---

## Step 2 — Bind a board

```sh
cd /path/to/your-project
tickets quickstart --agent boss --roles backend
# or, empty board only:
# tickets init
# tickets join boss --roles backend
```

`quickstart` is safe to run twice. It creates the board if needed, seeds a
sample epic, registers you, and prints the next three commands. Delete
samples when you are done: `tickets quickstart --remove`.

`tickets init` writes `.tickets/MASTER.md`, appends the protocol to
`AGENTS.md`, writes `.cursor/rules/tickets.mdc`, and (unless `--track`)
appends `.tickets/` to `.gitignore`. It **binds** or it refuses — if the
printed `board:` and later `tickets board` disagree, stop and read
[board-resolution.md](../board-resolution.md).

Linked worktrees of one repo share the main worktree's board. That is
deliberate. Do not "fix" it by creating a second `.tickets/` in a worktree.

---

## Step 3 — Become master (copy-paste)

Run as-is after Step 2. Replace names only if you must.

```sh
export TICKET_AGENT=boss
cd /path/to/your-project

tickets join "$TICKET_AGENT" --roles backend
tickets master take
tickets master                 # briefing + REVIEW QUEUE + HEALTH

# First board: write the template if it is missing
# (no-op with a message if MASTER.md already exists)
tickets master init

tickets objective "V1: offline gates green on main; deploy waits on credentials"
tickets objective              # confirm text + drive status
```

Fill `.tickets/MASTER.md` **before** you spawn anyone:

- Mission — one paragraph of what done looks like.
- Workforce — real agent names, not `example`.
- Current state — what is true *now* (this section wins over the decision
  log).
- Decision log — append only: `tickets master log "why I did X"`.

`handoff-check` fails if Mission still reads
`(what we are building, one paragraph)` or the workforce table still lists
`example`. A non-empty file is not a filled-in file.

```sh
python3 /path/to/tickets-clone/scripts/handoff-check.py
```

You do **not** take feature tickets. Master jobs each wake: unblock stuck
teammates, review + merge, coordinate (route, spawn, reopen silent claims).
`tickets merge` never pushes; you run `git push origin main`.

---

## Step 4 — Seed role context (E-013)

Standing context lives on the **board**. Watch and spawn inject it. It does
not store conversation and it is not a memory product. Dedicated operator
guide: [role-context.md](role-context.md). Product spec:
[pm-atman-role-context-v1.md](../product/pm-atman-role-context-v1.md).

| File | Who sees it | How you write it |
|---|---|---|
| `.tickets/briefs/_shared.md` | every seat | edit the file |
| `.tickets/briefs/roles/<role>.md` | seats whose `join --roles` include `<role>` | `tickets brief --role <role> "…"` or `--file` |
| `.tickets/briefs/<agent>.md` | that **worker** on `tickets prompt` / claim | `tickets brief <agent> "…"` or `--file` |

**Path lock:** inject reads only those paths. Repo-root `roles/` and
`$TICKETS_ROLES_DIR` are **not** sources. `roles/_shared.md` and
`roles/backend.md` in this repo are templates for humans. Copy ideas from
them; do not expect watch/spawn to load them.

Seed on a fresh board:

```sh
# Shared baseline — create once; inject reads this path only
mkdir -p .tickets/briefs/roles
cat > .tickets/briefs/_shared.md <<'EOF'
# Shared seat context

- One ticket at a time. Own worktree. Board-only comms (`tickets msg`).
- If blocked: `tickets msg "stuck: …" --to <master> --re <id>` early.
- Do not edit `.tickets/` by hand. Do not run `tickets clear`.
EOF

# Lane files — append (creates the file) or replace from --file
tickets brief --role backend "Implementation and wiring. Ship change + tests. Do not hold review."
tickets brief --role docs "Docs and operator guides. Do not take backend tickets."

# Per-agent standing brief (optional). Injected on the *worker* prompt and
# listed first on a claim. Master/cos prompts do not include this file —
# use _shared.md + roles, or spawn --brief, for those seats.
tickets brief scribe "House style: short sentences. No new APIs."

# Confirm what inject will see (run bare — do not pipe tickets through head/tail)
tickets brief --role backend --show
tickets prompt --master --agent boss     # master / drive prompt + role context
```

`tickets brief --role` **appends** a timestamped line. `--file` **replaces**
the whole file. `--role _shared` is refused — edit `_shared.md` yourself.
Exactly one target: agent name, `--role`, or `--ticket`.

Missing role file is not an error. That lane simply has no extra paragraph.
Do not treat silence as "it loaded the template."

Briefs are operator-written standing instructions. Durable decisions, evidence,
failures, runbooks, and skills live in the separate repo-backed
[knowledge graph](../knowledge/README.md). Relevant graph summaries enter the
same prompt renderer under a strict budget; tickets may reference their IDs.

```sh
tickets knowledge validate
tickets knowledge query "current component task" --agent boss
tickets brief --role backend --show   # what inject will actually send
```

---

## Step 5 — Dry smoke (no paid burn)

Prove join → pending → watch → prompt inject **before** you spawn Claude /
Codex / Cursor. A custom harness is a shell template. The runtime only
cares about exit code.

```sh
# Write a dry runner next to the project (or anywhere on disk)
cat > /tmp/atman-dry-harness.py <<'PY'
#!/usr/bin/env python3
"""Dry BYOA harness: prove watch/spawn without a paid model.

tickets expands {prompt_file} {cwd} {agent} and runs this through /bin/sh.
"""

import pathlib
import sys


def main(argv):
    # argv: script, prompt_file, cwd, agent — same order as the --cmd template
    prompt_file, cwd, agent = argv[1], argv[2], argv[3]
    text = pathlib.Path(prompt_file).read_text(encoding="utf-8")
    print("prompt is %6d bytes; cwd %s; agent %s" % (len(text), cwd, agent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
PY
chmod +x /tmp/atman-dry-harness.py

tickets join smoke --roles backend \
  --harness custom \
  --cmd '/tmp/atman-dry-harness.py {prompt_file} {cwd} {agent}'

tickets harness check smoke
# verdict is exit status only. "replied: no OK" is fine for this stub.
tickets prompt --agent smoke             # worker prompt: _shared + backend role

# One-shot wake (cron form). Needs something pending in this agent's lane.
# After quickstart the sample tickets are already role=backend — skip create.
tickets create "Dry inject check" --role backend
tickets watch --agent smoke --every 5 --max-runs 1 --run-timeout 1 \
  --cwd . \
  --exec '/tmp/atman-dry-harness.py {prompt_file} {cwd} {agent}'
```

The prompt file is written fresh every run and deleted after. Do not cache
its path. If `Role context` and the `_shared.md` path are in the captured
prompt, inject is wired. Full contract: [byoa.md](../byoa.md).

`--dry-run` on `watch` prints the command and does not exec it — useful, but
it does **not** prove the harness binary.

---

## Step 6 — Spawn and watch workers

When the dry path is green:

```sh
# Persistent worker: join + .worktrees/<name> + detached watcher
tickets spawn scribe --model sonnet --roles docs --brief "house style: short sentences"
tickets spawn core --model opus --roles backend --cost high

# Master seat that keeps planning (heartbeat even when nothing is pending)
tickets drive "V1: offline gates green on main; deploy waits on credentials" \
  --as boss --heartbeat 30 --tool cursor+claude

# Or spawn the planner without drive:
# tickets spawn boss --master --model sonnet --heartbeat 30

tickets spawn --list
tickets who
```

`--wake-mode task-only|continuous|scheduled` is a durable seat policy. Current
master and CoS seats default to `continuous`; other seats default to
`task-only`. A continuous adapter remains alive and treats direct DMs and named
mentions as task wakes. Scheduled adapters remain alive for explicit tasks and
assignments; the mode itself does not create a clock. Configure an objective
heartbeat or external cadence separately. The policy does not select a model
or harness.

For a model session hosted elsewhere, register `--harness remote` and install
`tickets hooks remote`. The schema-2 manifest exposes identity-pinned
register/heartbeat/long-poll claim/start/end/release commands under one fenced
lease. A bare remote `tickets spawn` fails closed: queued work stays visible in
the dashboard until the real bridge reconnects.

`spawn` with no `--harness` uses whatever `join` registered. Passing
`--harness` overrides **and** re-registers; a harness switch without a new
`--cmd` **drops** the old command template on purpose.

`tickets watch --once` is the cron/launchd form (exit 0 = there was work).
A session cannot be woken by a hook after its turn ends — that is why waking
is a poll plus `--exec`, not a callback.

Interactive Claude: `tickets hooks claude --agent boss` in the intended
worktree, then start `claude`. One-command enroll: `tickets boot --agent boss
--tool claude`.
Details: `tickets guide`, [connect-claude.md](../connect-claude.md).

---

## What not to expect

- **A shared-memory brain.** Briefs are standing instructions and the knowledge
  graph is reviewed evidence. Neither persists chat, raw prompts, or tool traces.
- **Repo-root `roles/` on inject.** E-013 path lock. Templates only.
  Repo-root `roles/` remains template-only. Relevant `knowledge/` summaries use
  the separate bounded graph selector.
- **The runtime to drive the model's inner loop.** It hands a prompt file
  and a cwd. Reasoning, tools, retries, and stop are the harness.
- **Parsed harness stdout.** Only exit code (backoff / `harness check`).
  Everything the board knows, an agent wrote with `tickets`.
- **Credential management.** API keys and `/login` are yours.
  `tickets limit` is how you tell the board someone is out.
- **Sandboxing.** `--safe` only changes built-in CLI permission flags.
- **Hooks for a custom harness.** Claude / Codex / Cursor only.
  Custom agents get context from the prompt file.
- **`tickets` to `git push`.** Merge fast-forwards local main after tests.
  You push.
- **Master to claim feature tickets.** Unblock, review, merge, route, spawn.
- **A fourth onboarding file.** A fresh master reads
  `.tickets/MASTER.md`, `HANDOFF.md`, `tickets map`, then
  `.tickets/briefs/_shared.md`. If that is not enough, fix those files.
  See [handoff-contract.md](../handoff-contract.md).

---

## Footguns

**PATH shim vs clone.** `~/.local/bin/tickets` may be a live-release
launcher. Your clone can be newer or dirtier. Always `tickets --version`
and `readlink -f $(which tickets)` in the session that will spawn workers.
Watchers put `~/.local/bin` at the **front** of `PATH`.

**Role mismatch.** `tickets quickstart` seeds `role=backend` sample tickets
and registers you with `--roles backend`. A BYOA seat joined `--roles docs`
will not be handed those tickets and will not get
`.tickets/briefs/roles/backend.md` in the prompt. `tickets route` /
`tickets next` follow `roles.json` from `join`/`spawn`, not the ticket title.
See [byoa.md](../byoa.md) — register the same lane you intend to work.

**Empty role brief honesty.** No file → no "Role context (…/roles/x.md)"
block. Inject does not fall back to `roles/backend.md` in the repo. If you
wanted house rules in every backend wake, you forgot to seed
`.tickets/briefs/roles/backend.md`.

**Default gitignore vs standing files.** `tickets init` (no `--track`)
appends `.tickets/` — a trailing-slash ignore. Git does not descend, so
`!.tickets/MASTER.md` does nothing. Live JSON should stay local; hand-written
standing files must be tracked. Use this pattern (see the handoff contract):

```gitignore
.tickets/*
!.tickets/MASTER.md
!.tickets/AGENT-HANDLES.md
!.tickets/AUTOMATION-BUDGET.md
!.tickets/briefs/
```

`--track` commits the **whole** board (locks, ticket JSON, identities).
That is the other extreme. Prefer the split above.

**Never overwrite MASTER.md without reading it.** Append with
`tickets master log`. An untracked MASTER.md has no undo.

**`$TICKETS_DIR` wins everything.** An old export from another project
sends every command to the wrong board. Unset it or set it on purpose.

**Two agents, one worktree.** `spawn` gives `.worktrees/<name>`. Do not
point two live seats at the same tree.

**Paid smoke.** `harness check` and `watch` run the **real** command.
A Claude/Codex check burns a real call. Use the dry harness in Step 5.

**`tickets prompt` is the worker prompt.** `master take` does not flip it.
Use `--master` or `--cos` to preview those seats. A watcher spawned without
`--master` will run the worker loop even if that agent is the current
master.

**`done` / `review` refuse from `main` and refuse a dirty tree.** Work on
the agent's branch. Notes are required (`--no-notes` only if there is
genuinely nothing to hand off).

---

## Reference

### Seats

| Seat | How you create it | Prompt | Wakes on |
|---|---|---|---|
| Worker | `join` / `spawn` | worker loop: inbox → mine → next → review | DMs, held ticket, ready work in its roles |
| Master | `master take` or `spawn --master` / `drive` | unblock, review+merge, coordinate. No feature tickets | review queue, `stuck:` / `blocked:`, CRIT health, heartbeat if `--heartbeat` |
| Master planner | master while a chief of staff is set | scope, route by cost/model, staffing. Does not merge unless cos is silent | same as master, plus vision drift |
| Chief of staff | `master cos <name>` or `spawn --cos` | review, unblock, merge. Escalates scope to the planner | same pending keys as master |

`tickets drive "<objective>" --as boss --heartbeat 30` = set objective +
`spawn boss --master --heartbeat 30`. Only seats with a heartbeat wake when
the board is quiet. The Claude Stop hook ignores heartbeats so an
interactive session is not pinned open.

### Prompt inject order (watch / spawn / `tickets prompt`)

One renderer. Built-in CLIs and `{prompt_file}` get the same text.

**Every seat, first:**

1. `.tickets/briefs/_shared.md` (if non-empty)
2. `.tickets/briefs/roles/<role>.md` for each role in `roles.json` for that
   agent, in listed order (skip `_shared`, skip dupes, skip missing files)
3. Relevant repo-backed `knowledge/` subgraph, deduplicated and bounded

**Then, workers only** (`tickets prompt`, or watch/spawn without `--master` /
`--cos`):

4. Standing brief `.tickets/briefs/<agent>.md`
5. Heartbeat / standing-seat block, if `drive_every` is set and an
   objective is open
6. Ticket context notes (`tickets brief --ticket <id>`) on held tickets
7. Any extra text the caller passed

**Then, master / planner / cos** (`tickets prompt --master` / `--cos`, or
`spawn --master` / `--cos`):

3. Drive block (objective + status + "advance the plan or log why not"),
   if an objective is open
4. Master vs planner prompt (planner if a cos exists and this seat is not
   the cos)
5. Cos prompt is the master prompt, rewritten for review/unblock/merge

`tickets master take` records who holds the seat. It does **not** change
what `tickets prompt` prints. Unattended master wakes must use
`spawn --master` or `tickets prompt --master`.

Truncation: role files and agent briefs cap at 6000 characters each, then
`...(role context truncated; read the file)`.

### Folder map — tracked vs local

Default `init` gitignores all of `.tickets/`. For a board you will hand
over, track standing files and ignore state (pattern in Footguns).

| Path | What | Typical git |
|---|---|---|
| `.tickets/T-*.json` | one file per ticket | local |
| `.tickets/epics/`, `sprints/` | epic / sprint records | local |
| `.tickets/agents/` | check-in, watch pid/log, harness check | local |
| `.tickets/roles.json`, `workforce.json` | who exists, roles, harness, cost | local |
| `.tickets/messages.jsonl` | append-only comms | local |
| `.tickets/objective.json` | standing objective | local |
| `.tickets/trajectories.jsonl` | turns / cost events (no prompt text) | local |
| `.tickets/merge.json` | optional merge test command | local or tracked if you care |
| `.tickets/MASTER.md` | mission, workforce, current state, log | **track** |
| `.tickets/MASTER.decisions.archive.md` | overflow log | track if present |
| `.tickets/CONTEXT.md` | optional claim-time briefing | track if you use it |
| `.tickets/briefs/_shared.md` | every-seat context | **track** |
| `.tickets/briefs/roles/<role>.md` | lane context (E-013 inject) | **track** |
| `.tickets/briefs/<agent>.md` | per-agent standing brief (worker prompt / claim) | **track** |
| `.tickets/AGENT-HANDLES.md` | spawn/resume ids | **track** |
| `.tickets/AUTOMATION-BUDGET.md` | spend cap notes | track if present |
| `HANDOFF.md` (repo root) | what is true about the work | **track** |
| `docs/handoffs/AGENT_CONTEXT.md` | extra claim briefing (if used) | track if present |
| `roles/*.md` (repo root) | templates only; **not injected** | tracked in this repo |
| `knowledge/` | durable graph facts, evidence, runbooks, and skills | **track** |
| `docs/knowledge/` | graph schema and operator documentation | **track** |
| `.worktrees/<agent>/` | that agent's git worktree + branch | local (git worktree) |
| `AGENTS.md`, `.cursor/rules/tickets.mdc` | protocol for Codex / Cursor | track |

`tickets context` prints MASTER.md, CONTEXT.md, and
`docs/handoffs/AGENT_CONTEXT.md` when those files exist. Per-agent briefs
are listed first on a claim via `context_paths`.

Trajectories: [trajectories.md](../trajectories.md). Board discovery:
[board-resolution.md](../board-resolution.md).

### Commands you will actually type

```sh
tickets master              # briefing
tickets master take         # become it
tickets master log "…"      # decision log
tickets inbox               # messages to you
tickets plan                # JSON keys + deps → real --after edges
tickets dep T-004 --after T-003
tickets map                 # sprint → epic → tickets
tickets graph               # dependency tree
tickets update T-002 "…"    # follow-up every 45m
tickets here                # still here
tickets reopen T-002        # silent >90m claims
tickets drive               # toward the objective
tickets dash --once         # status picture
tickets route [--claim]     # suggest / assign owners
tickets limits              # who is out (AUTH vs wait)
tickets merge               # integration worktree → tests → ff main
tickets spawn --list        # watchers
tickets spawn <name> --stop
tickets brief --role docs --show
tickets knowledge
tickets knowledge show kb-lock
tickets prompt --master --agent boss
tickets prompt --agent smoke
```

Worker loop and merge rules stay in the [README](../../README.md). Verbs
people miss (`pulse`, `reopen`, `handover`): [cli-gotchas.md](../cli-gotchas.md).
Operating rules to paste into `_shared.md`:
[agent-operating-rules.md](../agent-operating-rules.md).
