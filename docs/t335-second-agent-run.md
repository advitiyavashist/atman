# T-335 — second-agent README-only acceptance run

Verifier: **composer** (T-335). Author under test: **opus-console** (T-322,
`opus-console/t322-quickstart@24d8280`). I had **not** read `tickets.py` before
step 4 of the procedure.

## Timing

| Milestone | UTC timestamp |
|---|---|
| Clone start | 2026-09-07T14:12:39Z |
| Clone finished | 2026-09-07T14:13:47Z |
| `tickets quickstart` finished | 2026-09-07T14:14:11Z |
| `tickets next` claimed T-001 | 2026-09-07T14:14:14Z |

**Wall clock clone-finished → ticket claimed: 27 seconds** (budget: 300 s).

Total clone start → claim: **95 seconds** (clone dominated by network).

## Environment

- Machine: macOS, Python 3.9 system + steer `.venv` 3.12 for unrelated work
- Fresh clone: `/tmp/t335-clone` (branch `opus-console/t322-quickstart`)
- Fresh project: `/tmp/t335-project` (empty, later `git init` for comparison)
- `TICKETS_DIR` was set in the agent shell to the live steer board — **unset**
  before the successful run (see deviation D2)

## Procedure transcript

### Step 0 — Install equivalent (README lines 17–20)

README Install:

```sh
git clone <this repo> ~/tickets
ln -s ~/tickets/tickets.py ~/.local/bin/tickets
cd your-project && tickets quickstart
```

Procedure mandated clone at `/tmp/t335-clone` instead of `~/tickets`. To avoid
overwriting the operator's live `~/.local/bin/tickets`, symlinked to a temp bin:

```console
$ mkdir -p /tmp/t335-bin
$ ln -sf /tmp/t335-clone/tickets.py /tmp/t335-bin/tickets
$ export PATH="/tmp/t335-bin:$PATH"
```

### Step 1 — First quickstart attempt (README Quickstart lines 30–33)

Jumped to the Quickstart section before completing Install with the clone's
binary:

```console
$ cd /tmp/t335-project
$ tickets quickstart --agent t335-alice
tickets: error: argument cmd: invalid choice: 'quickstart'
EXIT:2
```

**Deviation D1:** Used ambient `~/.local/bin/tickets` (steer pin without
`quickstart`). README order is Install *then* Quickstart; a reader who skips
Install hits this. **Fix:** Quickstart section should cross-link Install (“run
the Install symlink step first”) or fail with “install this repo's tickets.py”.

### Step 2 — Successful quickstart (after unset TICKETS_DIR)

```console
$ unset TICKETS_DIR
$ export PATH="/tmp/t335-bin:$PATH"
$ cd /tmp/t335-project
$ tickets quickstart --agent t335-alice
board: /private/tmp/t335-project/.tickets
  resolved from the current directory (not inside a git worktree)
wrote: /private/tmp/t335-project/.cursor/rules/tickets.mdc
wrote: /private/tmp/t335-project/AGENTS.md
wrote: /private/tmp/t335-project/.gitignore
wrote: /private/tmp/t335-project/.tickets/MASTER.md
bound: `tickets` run from /private/tmp/t335-project resolves to this board.
...
joined as t335-alice  roles=['backend']  can=-  cost=medium

The three commands that matter:
  TICKET_AGENT=t335-alice tickets next                      claim the next ready ticket
  TICKET_AGENT=t335-alice tickets update <id> "..."         say where you are, at least every 45 min
  TICKET_AGENT=t335-alice tickets review <id> --notes "..." hand it back with evidence

See it: tickets ui        ->  http://127.0.0.1:8765   (read-only, auto-refresh)
Learn it: tickets guide   |   docs/first-session.md   |   README.md
EXIT:0
```

**Deviation D2:** First attempt with `TICKETS_DIR` set refused init (T-263
guard). README does not mention unsetting `TICKETS_DIR`. Fresh users without
steer env: N/A. Steer dev boxes: **document** “unset TICKETS_DIR for a local
board” in README or quickstart error text.

### Step 3 — Claim first ticket

```console
$ export TICKET_AGENT=t335-alice
$ tickets next
[>] IN PROGRESS T-001  Sample: design the data model  (E-001; role=backend; owner=t335-alice)
...
EXIT:0
```

### Step 4 — Second quickstart (idempotent)

```console
$ tickets quickstart --agent t335-alice
samples: already here (T-001, T-002, T-003) -- not creating them again
EXIT:0
```

No duplicate samples. **PASS.**

### Step 5 — Dependency gate

```console
$ tickets next --another
no ticket ready: 2 open, all waiting on unfinished work (in progress with: t335-alice)
```

Matches README lines 39–41. **PASS.**

### Step 6 — `--with-agent` harness detection

```console
$ tickets quickstart --with-agent t335-alice
worker: claude will run as t335-alice
  tickets spawn t335-alice --tool claude
  (not launched for you -- quickstart never starts a background process without asking; ...)
```

Detected **claude** harness on this machine. Spawn line printed, not launched.
**PASS.**

### Step 7 — UI URL

```console
$ tickets ui &
$ curl -s -o /dev/null -w "HTTP:%{http_code}\n" http://127.0.0.1:8765/
HTTP:200
```

Quickstart prints `http://127.0.0.1:8765`; server is **not** auto-started (README
line 43: “Watch it live with `tickets ui`”). URL works once started. **PASS**
with note that quickstart only prints the URL.

## Deviations summary

| # | README line / section | What happened | Required reading code? |
|---|---|---|---|
| D1 | Quickstart § (before Install done) | Ambient `tickets` lacks `quickstart` | No — retry after Install symlink |
| D2 | (not documented) | `TICKETS_DIR` blocked init | No — unset env var |
| D3 | Install § clone path | Procedure used `/tmp/t335-clone` not `~/tickets` | No — procedure override |
| D4 | (not documented) | Symlink to `/tmp/t335-bin` not `~/.local/bin` | No — avoid clobbering live pin |

**Count: 4 deviations, 0 required reading source code.**

## Step 4 (procedure) — `docs/first-session.md` vs this run

| Topic | first-session.md | This run | Match? |
|---|---|---|---|
| Starting state | “fresh **git** repo” | empty dir, no `git init` initially | **NO** — README never says `git init` |
| Board resolution line | “git worktree cwd is in” | “not inside a git worktree” until `git init` | **NO** |
| quickstart flags | `--agent alice --roles backend` | `--agent t335-alice` (README example) | **NO** — `--roles` only in first-session |
| `next` worktree RULE | printed (branch `main`) | missing without `git init`; appears after `git init` | **NO** |
| Three commands / UI URL / sample chain | same | same | **YES** |
| Second agent blocked | same message | same | **YES** |

**Finding F1:** README Quickstart should say `git init` (or “use an existing git
repo”) so output matches `docs/first-session.md` and the worktree RULE appears.

## Step 5 — T-323 UI cross-check (composer/t323-onboarding @ 37c81ec)

T-323 checklist display strings vs README quickstart:

| T-323 label | T-323 cmd hint | README says | Mismatch? |
|---|---|---|---|
| Board ready | `tickets quickstart --agent <you>` | same (line 32) | no |
| Work on the board | `tickets quickstart` | `tickets next` to claim (line 33) | **YES** |
| You registered | `tickets quickstart --agent <you>` | same | no |
| First review submitted | `tickets review <id> --notes "..."` | same (line 35) | no |

**Finding F2:** T-323 step `first_ticket` cmd should be `tickets next`, not
`tickets quickstart`.

Empty-board CTA (`tickets quickstart --agent <you>`) matches README. Next-step
strip empty-board hint matches README Install/Quickstart.

## Cleanup

```console
$ lsof -ti:8765 | xargs kill -9    # UI from smoke test
$ ps … | grep 'tickets watch'      # none
```

`/tmp/t335-clone`, `/tmp/t335-project`, `/tmp/t335-bin` left for master review;
delete when done.

## VERDICT

**PASSES** — first ticket claimed in **27 s** after clone (well under 5 min).
No deviation required reading `tickets.py`. Four deviations are install-order,
env, or procedure-path; two doc findings (F1 git init, F2 T-323 checklist cmd)
should be fixed but do not block the core T-322 criterion.
