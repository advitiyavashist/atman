# T-819 README proof: executable path and browser evidence

Date: 2026-09-15. Tree: `atman-landing-fable-0914` on top of Atman
`origin/main` e201436. Runtime: this checkout's `tickets.py`, installed with
`./install.sh --prefix <scratch>/bin` and that prefix on PATH.
`TICKETS_DIR`, `TICKET_AGENT` and `TICKET_SEAT` were unset before every run;
`atm where` confirmed the throwaway board. The live board was never touched.
Paths under the scratch directory are shown as `<scratch>`.

Two throwaway boards were used. Board 1 ran the `atm plan` path from the
accepted T-969 draft and stopped at two refusals. Board 2 ran the
`atm create --deps` path that README.md now documents, end to end.

## Board 1: the plan path stops at review

Findings, matching the T-972 proof run on the same runtime:

- `atm review` refuses a sounded code ticket without `--pr N`; `--force`
  does not bypass it. `atm plan` sounds what it writes.
- `atm done` from a seat that is neither the owner nor master is refused
  with a stale-ownership message (`boss` had not taken master here).
- `atm accept` refuses a ticket that is not IN REVIEW.

```

$ atm quickstart --remove
removed 3 sample ticket(s) (T-001, T-002, T-003)
epic E-001 left in place (it may hold your own work now)
committed quickstart files

$ atm objective Add a CSV summary and a command that uses it
objective set
FLAG: no measurable exit criterion; add --exit "<observable end state>"

$ atm plan <<'EOF' ... EOF
created T-001  CSV summary function  lane=ready
created T-002  CLI command that calls summarize()  lane=ready

$ atm graph
Dependency graph (2 open)
`- [ ] T-001 CSV summary function  (backend)
   `- [ ] T-002 CLI command that calls summarize()  (backend; waiting on T-001)

$ atm join worker --roles backend
joined as worker  roles=['backend']  can=-  cost=medium  harness=claude (default)  wake=task-only  lifecycle=ephemeral  alias=-
board: <scratch>/proj/.tickets
master: nobody -- `tickets master take` if you are it

RULE: you are on branch 'main' in the primary worktree. Work on your own tree:
  git -C <scratch>/proj worktree add .worktrees/worker-work -b worker-work
  cd <scratch>/proj/.worktrees/worker-work

Loop:  atm master  ->  atm next  ->  work + commit  ->  atm update <id> "..." (every 45 min)  ->  atm done <id> --notes "..."  ->  merge  ->  atm next
Full instructions: atm connect --worker
worktree .worktrees/worker on branch worker

$ atm next
[>] IN PROGRESS T-001  CSV summary function  (role=backend; owner=worker; lane=ready)

Read this briefing before editing:
  <scratch>/proj/.tickets/MASTER.md

Board: <scratch>/proj/.tickets  (cwd is <scratch>/proj/.worktrees/worker)
Waiting on this: T-002 CLI command that calls summarize()
Time: active 0m, waited 0m before claim, last update 0m ago
lane: ready
sounded: 2026-09-15T00:54:26Z by boss
Cause: B needs summarize()
Change: Write summarize() and one test
Proof: test passes

## Cause or spec
B needs summarize()

## Change
Write summarize() and one test

## Proof
test passes

## Deps
none

## Open questions
(none)


Notes:
  - [boss] sound: deps=none

Post progress with `tickets update T-001 "..."` at least every 45 min; finish with `tickets done T-001 --notes "branch@sha, paths, decisions"`.
{'rows': 1, 'columns': 2}
committed b4f0784

$ atm review T-001 --notes csv_summary.py summarize(path); test_csv_summary.py passes
review: sounded code tickets require --pr N (or role=docs|pm and body says "no PR")

--- if review refused without --pr, retry with --force:
review: sounded code tickets require --pr N (or role=docs|pm and body says "no PR")
full sha: b4f0784e862ff0065c7ba0e74bd2d9f4d3c527bf

$ atm accept T-001 --sha b4f0784e862ff0065c7ba0e74bd2d9f4d3c527bf --notes test passes
T-001 is IN PROGRESS; only IN REVIEW work can be accepted

$ atm done T-001 --notes summarize(path) -> dict in csv_summary.py
T-001 is owned by worker under generation 1; boss cannot done (stale ownership after restart or reassignment)

--- if done refused on main, retry from worker worktree with boss seat:
T-001 is owned by worker under generation 1; boss cannot done (stale ownership after restart or reassignment)

$ atm next
you already hold T-001 -- finish it (tickets done/block/reopen) before claiming more, or pass --another if you really want to work two in parallel.

$ atm who
agent          state     loop-seen branch@sha                     ticket               worktree
worker         unknown ? 0m ago   worker@8b92cb3                 T-001 [>]            <scratch>/proj/.worktrees/worker
               no Claude or Codex transcript for /private/tmp/claude-502/-Users-<user>-Downloads-atman--worktrees-a
               lifecycle=ephemeral provider=claude session=- reachable=no
boss           unknown ? 1m ago   main@eff19e2 +3                -                    <scratch>/proj
               no Claude or Codex transcript for /private/tmp/claude-502/-Users-<user>-Downloads-atman--worktrees-a
               "joined"
               lifecycle=ephemeral provider=claude session=- reachable=no
!! on main/master: boss -- rule 4, move to a worktree

state is read from the session's own transcript, not from loop-seen.  ! asserted by a human   ~ heuristic (run cadence only)   ? unknown -- go read the log
```

## Board 2: the create path closes locally

Sequence: `atm master take`, objective with `--exit`, two `atm create`
tickets with `--deps`, worker joins and claims A in its own worktree,
commits, `atm review` pins `worker@76279da`, boss accepts on the full SHA,
boss runs `atm done`, B unblocks, worker claims B and the claim prints
`Handoff from dependencies (all notes):` with A's review and done notes.

```

$ atm quickstart --agent boss --roles backend
board: <scratch>/proj2/.tickets
  resolved from the git worktree cwd is in (<scratch>/proj2)
wrote: <scratch>/proj2/.cursor/rules/tickets.mdc
wrote: <scratch>/proj2/AGENTS.md
wrote: <scratch>/proj2/.gitignore
wrote: <scratch>/proj2/.tickets/MASTER.md
bound: `tickets` run from <scratch>/proj2 resolves to this board.

Claude Code: install a scoped hook with `tickets hooks claude --agent <name>`.
Codex and Cursor read AGENTS.md; Cursor also gets .cursor/rules/tickets.mdc.

board: <scratch>/proj2/.tickets
epic:  E-001  Sample epic: a first slice end to end
  T-001  Sample: design the data model              ready now
  T-002  Sample: build the API on top of the model  after T-001
  T-003  Sample: put a screen on the API            after T-002
joined as boss  roles=['backend']  can=-  cost=medium  harness=claude (default)  wake=task-only  lifecycle=ephemeral  alias=-

The three commands that matter:
  TICKET_AGENT=boss tickets next                      claim the next ready ticket
  TICKET_AGENT=boss tickets update <id> "..."         say where you are, at least every 45 min
  TICKET_AGENT=boss tickets review <id> --notes "..." hand it back with evidence

See it: tickets ui        ->  http://127.0.0.1:8765   (read-only, auto-refresh)
Learn it: tickets guide   |   docs/first-session.md   |   README.md

$ atm quickstart --remove
removed 3 sample ticket(s) (T-001, T-002, T-003)
epic E-001 left in place (it may hold your own work now)
committed quickstart files

$ atm master take
boss is master now. Run `tickets master` for the briefing.
WARNING: boss took master with no live endpoint. Mail stays queued until a native session or persist watcher is online.

$ atm objective Add a CSV summary and a command that uses it --exit summary command prints row and column counts
objective set

$ atm create CSV summary function --role backend --body Cause: B needs summarize(). Change: write summarize(path) and one test. Proof: test passes.
created T-001  CSV summary function

$ atm create CLI command that calls summarize() --role backend --deps T-001 --body Cause: A ships summarize(). Change: add the command. Proof: command prints the summary.
created T-002  CLI command that calls summarize()
  waits for: T-001

$ atm graph
Dependency graph (2 open)
`- [ ] T-001 CSV summary function  (backend)
   `- [ ] T-002 CLI command that calls summarize()  (backend; waiting on T-001)

$ atm join worker --roles backend
joined as worker  roles=['backend']  can=-  cost=medium  harness=claude (default)  wake=task-only  lifecycle=ephemeral  alias=-
board: <scratch>/proj2/.tickets
master: boss

RULE: you are on branch 'main' in the primary worktree. Work on your own tree:
  git -C <scratch>/proj2 worktree add .worktrees/worker-work -b worker-work
  cd <scratch>/proj2/.worktrees/worker-work

Loop:  atm master  ->  atm next  ->  work + commit  ->  atm update <id> "..." (every 45 min)  ->  atm done <id> --notes "..."  ->  merge  ->  atm next
Full instructions: atm connect --worker
worktree .worktrees/worker on branch worker

$ atm next
[>] IN PROGRESS T-001  CSV summary function  (role=backend; owner=worker)

Read this briefing before editing:
  <scratch>/proj2/.tickets/MASTER.md

Board: <scratch>/proj2/.tickets  (cwd is <scratch>/proj2/.worktrees/worker)
Waiting on this: T-002 CLI command that calls summarize()
Time: active 0m, waited 0m before claim, last update 0m ago
lane: ready

Cause: B needs summarize(). Change: write summarize(path) and one test. Proof: test passes.

Post progress with `tickets update T-001 "..."` at least every 45 min; finish with `tickets done T-001 --notes "branch@sha, paths, decisions"`.
committed 76279da

$ atm review T-001 --notes csv_summary.py summarize(path); test_csv_summary.py passes
pinned worker@76279da in <scratch>/proj2/.git (this checkout -- pass --artifact <dir> if the deliverable is in another repo)
T-001 -> IN REVIEW after 0m of work; master (boss) notified. Claim your next ticket.
wake: boss -> inbox-posted
full sha: 76279dac836524d0715d345ad6fb5f6cbc52d01e

$ atm accept T-001 --sha 76279dac836524d0715d345ad6fb5f6cbc52d01e --notes ran test_csv_summary.py: passes
T-001 accepted 76279dac836524d0715d345ad6fb5f6cbc52d01e by boss

$ atm done T-001 --notes summarize(path) -> dict in csv_summary.py
T-001 done in 0m (waited 0m before claim)
recorded main@dc3aad6
unblocked: T-002
started: T-002
wake: boss -> inbox-posted

--- if done was refused, try merge:
nothing to merge: no branches given and the review queue is empty

$ atm show T-001
[x] DONE        T-001  CSV summary function  (role=backend; owner=worker)

Read this briefing before editing:
  <scratch>/proj2/.tickets/MASTER.md
Waiting on this: T-002 CLI command that calls summarize()
Time: active 0m, waited 0m before claim
lane: ready

Cause: B needs summarize(). Change: write summarize(path) and one test. Proof: test passes.

Review events:
  - [boss] accept 76279dac836524d0715d345ad6fb5f6cbc52d01e -- ran test_csv_summary.py: passes

Notes:
  - [worker] REVIEW: worker@76279da -- csv_summary.py summarize(path); test_csv_summary.py passes
  - [boss] main@dc3aad6 -- summarize(path) -> dict in csv_summary.py

$ atm next
[>] IN PROGRESS T-002  CLI command that calls summarize()  (role=backend; owner=worker; after T-001)

Read this briefing before editing:
  <scratch>/proj2/.tickets/MASTER.md

Board: <scratch>/proj2/.tickets  (cwd is <scratch>/proj2/.worktrees/worker)
Time: active 0m, waited 0m before claim, last update 0m ago
lane: ready

Cause: A ships summarize(). Change: add the command. Proof: command prints the summary.

Handoff from dependencies (all notes):
  T-001 (CSV summary function): REVIEW: worker@76279da -- csv_summary.py summarize(path); test_csv_summary.py passes
  T-001 (CSV summary function): main@dc3aad6 -- summarize(path) -> dict in csv_summary.py

Post progress with `tickets update T-002 "..."` at least every 45 min; finish with `tickets done T-002 --notes "branch@sha, paths, decisions"`.
```

## Browser evidence

`atm ui --port 8791` served board 2 from the coordinator's checkout;
`GET /board.json` answered 200. Captures with headless Google Chrome
(`--headless=new --screenshot --window-size`), poll until the PNG is stable,
then kill the process; one `--user-data-dir` per shot. Theme follows the OS
preference (dark).

| File | Viewport | What it shows |
| --- | --- | --- |
| `docs/brand/evidence/t819-app-1440.png` | 1440x1000 | Work view: objective "Add a CSV summary and a command that uses it" with its exit criterion; graph with T-001 DONE (@worker) and T-002 WORKING (@worker), "after 1 step"; median turns and yield@cost blank with "unknown is not zero"; onboarding 7/7; master boss |
| `docs/brand/evidence/t819-app-768.png` | 768x1100 | Same state at tablet width; header wraps, no horizontal overflow |

## What README.md claims and where each claim was checked

| README line | Checked by |
| --- | --- |
| Install block (`git clone`, `./install.sh`, `atm self`) | `install.sh --prefix` output above; `atm self` output above |
| `unset TICKETS_DIR`, `atm where`, `atm quickstart --agent boss --roles backend` | board 1 and board 2 output above |
| quickstart leaves `AGENTS.md`, `.gitignore`, `.cursor/` uncommitted | `git status --short` after quickstart on board 1 |
| First ticket path: create, join, worktree, next, review pin, accept, done, next with Handoff block | board 2 output above |
| `atm accept` refuses non-review state | board 1 output above |
| done/accept need master or owner | board 1 (refused) vs board 2 (master, accepted) |
| `atm done` records the commit of the checkout it runs in | board 2: `recorded main@dc3aad6` while the accepted SHA is 76279da |
| review refuses sounded code tickets without `--pr`; `--force` does not bypass | board 1 output above |
| Homebrew tap not published | `gh repo view advitiyavashist/homebrew-tap` on 2026-09-15: could not resolve |
| Antigravity experimental | T-988 root cause note on the board (masked 429; adapter passes no --model) |
| `atm ui` first screen | captures above |

Not proven here, stated as such in README.md: real agent processes behind
`atm spawn`/`atm watch` (the recording under `docs/demo/`, T-924), Codex
wake after one run, cross-provider recovery (T-976), remote control (T-818).
