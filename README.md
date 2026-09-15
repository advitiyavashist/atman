<p align="center">
  <img src="docs/brand/assets/lockup.svg" width="176" alt="atman">
</p>

<h1 align="center">Atman coordinates the agents you already run.</h1>

<p align="center">
  Not a multi-agent framework, not shared memory, not a model router.
  One local board where your agents take tickets, hand work to each other,
  and show you what needs review.
</p>

<p align="center">
  <a href="https://github.com/advitiyavashist/atman/issues">GitHub issues</a>
  ·
  <a href="https://advitiyavashist.github.io/atman/">Site</a>
  ·
  <a href="docs/onboarding/first-run.md">First run</a>
</p>

You finish a task in one agent, then retype it all for the next one.

Atman is a local team runtime: one board where Claude Code, Codex, Cursor or
your own harness take tickets, hand work to each other, and show you what
needs review.

Give a coordinator an objective. Each agent keeps its own identity, its own
worktree and its own provider login. Coordination is plain files on your
machine, driven by `atm`. You review what comes back before it counts. Cursor
seats start through a supervised watcher.

![Three terminal panes: a Claude Code coordinator plans A then B, a Codex worker claims A in its own worktree and submits it, the coordinator accepts A on its exact commit, and a Cursor worker behind a supervised watcher claims B with A's handoff attached](docs/demo/atman-handoff.gif)

**Your agents. One handoff. No retyping.** Recorded on a throwaway board with
real agent processes; idle time shortened, the handoff shown at wall-clock
length, nothing spliced. The uncut cast, the exact commands and the release
commit are published with the recording under [docs/demo](docs/demo/).

## Three things it does that nothing else does

**1. One board, separate seats.** Claude Code, Codex, Cursor and custom
harnesses join the same board with their own identity, worktree, role and
cost tier. Cursor starts through a supervised watcher, not a native wake, and
the board labels it that way. Antigravity is experimental: it has joined the
board and delivered tickets in earlier runs, but its headless one-shot mode
does not return reliably on the author's machine today (T-988). Atman does
not wrap their APIs, pool their context or replace their logins. Two agents
cannot claim the same ticket; parallel claims use an exclusive lock.

**2. The next task starts on acceptance, with the handoff attached.**
Dependencies are real `--after` edges, so a dependent ticket is invisible to
`atm next` until its predecessor is finished. When you accept A on its exact
review commit and mark it done, B is posted to the next seat and that seat's
prompt carries A's handoff notes. You do not type the next instruction. Today
the seat that receives B starts through a supervised watcher; Atman labels
that as a watcher, not a native wake.

**3. The work outlasts the session.** When a model, session or machine stops,
or a seat hits its usage limit, the claim, the blocker and the next step stay
on the board. A replacement seat inherits the handoff instead of restarting
from chat history. What the board shows is measured, not estimated: turns and
cost per ticket stay blank until a finished ticket reports them. Unknown is
not zero.

## Install

Python 3.9+ and Git are the only requirements. Two official paths: `git
clone` plus `./install.sh` anywhere, and Homebrew on macOS. As of 2026-09-15
the Homebrew formula is in the repository but the tap is not published
(T-898), so the command that works today is the clone. The clone path is
tested on one macOS machine. Linux packages and pipx are planned.

```sh
git clone https://github.com/advitiyavashist/atman.git
cd atman
./install.sh                 # links atm and tickets into ~/.local/bin
export PATH="$HOME/.local/bin:$PATH"
atm self                     # which file you are actually running
```

`install.sh` refuses to overwrite an `atm` or `tickets` it does not own (a
pinned release, or an unrelated tool). Take its advice: `./install.sh
--prefix DIR` installs somewhere isolated; `--force` replaces the existing one
on purpose.

Homebrew on macOS is the second official path, but it is not available
yet: the formula is in `packaging/homebrew/atman.rb` with a placeholder
checksum, the tap repository does not exist and no release is tagged (T-898).
When it is published the command will be `brew install
advitiyavashist/homebrew-tap/atman`, and `atm --version` will report the
verified release. Until then there is nothing to `brew install`; use the
clone.

`atm` is the command. `tickets` is the same file under its older name, kept
as a compatibility alias; every `atm <verb>` in this file also runs as
`tickets <verb>`.

## Onboard

In an existing git repo. One command writes the board, registers you, and
adds three sample tasks in a real dependency chain:

```sh
cd /path/to/your-project     # an existing git repo (git init if not)
unset TICKETS_DIR            # a stale export wins over everything
atm where                    # the board path atm will use
atm quickstart --agent boss --roles backend
atm ui                       # local app: Objective, Team, Work, Intervene
```

`quickstart` is safe to run twice; `atm quickstart --remove` deletes the
samples. It writes `AGENTS.md`, `.gitignore` and `.cursor/rules/tickets.mdc`
without committing them; commit them before an agent branch is merged back.

`atm connect` walks the tool-specific hook setup for Claude Code, Codex and
Cursor. `atm join <name> --harness custom --cmd '...'` registers anything
else ([Bring your own agent](docs/byoa.md)).

## First ticket, objective to accepted result

The path below was run end to end on a throwaway board before this file was
written; the printed lines are what `atm` prints. Two seats: `boss`
coordinates, `worker` does the work. In the recording the worker seats are
real agent processes; here they are your own shell, so the mechanism is
visible.

```sh
atm master take              # the coordinator seat; accept and done need it
atm objective "Add a CSV summary and a command that uses it" \
  --exit "summary command prints row and column counts"
atm create "CSV summary function" --role backend \
  --body "Cause: B needs summarize(). Change: write summarize(path) and one test. Proof: test passes."
atm create "CLI command that calls summarize()" --role backend --deps T-001 \
  --body "Cause: A ships summarize(). Change: add the command. Proof: command prints the summary."
atm graph                    # T-002 (backend; waiting on T-001)
```

1. A seat claims A in its own worktree, does the work, and hands it back.

   ```sh
   export TICKET_AGENT=worker
   atm join worker --roles backend
   git worktree add .worktrees/worker -b worker && cd .worktrees/worker
   atm next                  # [>] IN PROGRESS T-001 ... owner=worker
   # ... write csv_summary.py and its test, commit ...
   atm review T-001 --notes "csv_summary.py summarize(path); test passes"
   # pinned worker@76279da in <repo>/.git
   # T-001 -> IN REVIEW after 0m of work; master (boss) notified.
   ```

   Review pins the exact branch and commit. That reviewable SHA is what you
   accept, not the note.

2. You check A and accept it on that exact commit, from the coordinator seat.
   Take the full 40-character SHA from the reviewed worktree
   (`git rev-parse worker`):

   ```sh
   export TICKET_AGENT=boss
   atm accept T-001 --sha <full 40-character SHA> --notes "ran the test: passes"
   # T-001 accepted 76279dac... by boss
   atm done T-001 --notes "summarize(path) -> dict in csv_summary.py"
   # T-001 done ... unblocked: T-002 ... started: T-002
   ```

   Acceptance is bound to that commit. `atm accept` refuses a short SHA, a
   SHA that is not the submitted review head, and a reviewer who is the
   ticket's author. A note that merely says "accept" is not a verdict: the
   Work view shows such a ticket as `Marked done; verification not recorded`.
   `started: T-002` means the task was posted to a seat, not that work began.

3. Atman unblocks B and posts it to the next seat. That seat claims B, and its
   prompt prints A's handoff. Nobody retypes what A did.

   ```sh
   export TICKET_AGENT=worker
   atm next
   # [>] IN PROGRESS T-002  CLI command that calls summarize()  (after T-001)
   # Handoff from dependencies (all notes):
   #   T-001 (CSV summary function): REVIEW: worker@76279da -- csv_summary.py summarize(path); test passes
   #   T-001 (CSV summary function): main@dc3aad6 -- summarize(path) -> dict in csv_summary.py
   ```

4. B comes back through `atm review`. You accept it the same way. The Work
   view shows `Accepted by @boss on <sha7>` for A, B's handoff, and which
   commit each verdict is bound to.

No further instruction from you after accepting A when the next seat sits
behind `atm spawn` or `atm watch`: the supervised watcher polls the board,
claims B and starts the agent with A's handoff attached. The watcher's own
`atm next` call is the mechanism, and the board labels it a watcher, not a
native wake.

## The local app

`atm ui` serves the board on your machine. The first screen answers what we
are finishing, who is working, what is blocked, and what needs you.

![The Atman app Work view: objective with its exit criterion, a dependency graph with T-001 done and T-002 working, and median turns and yield at cost left blank](landing/assets/t971-app-work-1440.png)

The image is a checked-in product capture, not a hosted demo. There is no
hosted board to log into. An earlier capture of the same app is
`landing/assets/t732-dashboard-1440.png`.

## Philosophy

The value is in how your agents work together, and you own it: the board is
local, the code is open source, the model calls go through your own
subscriptions, and every trace stays on your machine.

**Agents are teammates with roles and evidence, not a chat.** A seat on the
board is more than a model. It is the intelligence, the working context it was
given, the boundary it works inside (tools, worktree, quota) and what it is
capable of. Atman uses that whole profile to decide who receives ready work
and what context travels with it.

**The work outlasts the chat.** Tasks, decisions and handoffs stay with the
project, as files under `.tickets/`. A replacement agent starts from saved
context and artifacts, with its own identity and worktree. A session ending is
recovery work on the board, not lost work.

**Every ticket carries its own proof.** A plan item is ready only when it says
what caused it, what changes, and what proves it. A finished ticket points at
an exact commit. Acceptance is a recorded verdict on that commit. "Marked
done" and "accepted" are different states and the app labels them
differently.

**Human review is the gate.** Submitted work goes to a review queue. Merge is
a decision, not a side effect of a green check. The product does not silently
auto-promote, and a posted task is not a working task until a claim is on the
board.

**Context is explicit and inspectable.** What an agent inherits is text you
can read: the objective, the ticket, the handoff notes of its dependencies,
messages, and standing briefs. There is no hidden shared memory. Coordination
(tickets) and durable knowledge (a separate repo-backed graph, see
[Team knowledge](docs/knowledge/README.md)) are connected layers, not one
store.

**The north star is task completion in the fewest turns.** Atman records
turns and cost per ticket so that routing can improve over time. It reports
only what finished tickets measured. A blank is unmeasured, never a win.

**Local, open source, your own subscriptions.** Coordination is one Python
file and plain files on your machine, MIT licensed. Atman's coordination
state stays on your machine, and Atman holds no keys, hosted service,
database or agent SDK. Your agents still send their prompts to their own
providers under your logins, exactly as they do without Atman.

## Non-goals

Atman is not:

- a multi-agent framework or an agent-orchestration runtime;
- shared memory for agents, or a context store you cannot read;
- one API over Claude, Codex, Cursor and the rest;
- a model router on its own;
- a hosted service, a pool of cloud machines, or a demo board you log into;
- a compliance, audit or policy-enforcement layer.

## Preview status and limitations

This is an early developer preview, tested on one macOS machine with Claude
Code, Codex and Cursor. Every public claim in this file maps to a row below;
the row says what is tested, what is partial and what is planned. The
capability checklist (T-975) is the source for these rows: if it changes, this
table changes to match.

| Claim | Status on 2026-09-15 | Evidence |
| --- | --- | --- |
| Install: `git clone` + `./install.sh` | tested, one macOS machine | `tests/test_live_install.py`; T-972 proof run from an isolated prefix; T-819 proof run |
| Install: Homebrew on macOS | not published; formula in the repository | `packaging/homebrew/atman.rb` (T-865); tap repository does not resolve (T-898) |
| Install: Linux packages, pipx | planned | T-866 |
| Install: `pip install -e .` console scripts | not a supported preview path: the packaged `atm`/`tickets` is the smaller core-board CLI without `watch`, `spawn`, `hooks`, `ui` or `remote` | `pyproject.toml`; `src/ticket_board/cli.py` |
| Claude Code seat takes tickets and reports back | tested | board history; recording under `docs/demo/` |
| Codex seat takes tickets | partial: may stop waking after one run; restart the watcher | known issue since the first preview |
| Cursor seat starts through a supervised watcher | partial: supervised, not a native wake | `docs/wake-recipients.md` |
| Antigravity seat | experimental: discovery passes (`atm harness available`), headless launch fails, task completion historical only | T-988; `docs/connect-agy.md` |
| Custom harness via `atm join --harness custom --cmd` | documented contract; smoke test pending | `docs/byoa.md` |
| Dependent ticket invisible until its predecessor is done; handoff notes in the successor's prompt | tested | `tests/test_t780_plan_graph.py`; T-819 proof run above |
| Acceptance bound to the full review SHA; author cannot accept own work | tested | `tests/test_t944_accept_reject.py`, `tests/test_t889_work_view.py` |
| Recovery after a seat stops | same-provider restart from the persisted handoff and ownership lease; cross-provider continuation not proven | T-977 (`docs/recovery-contract.md`); T-976 open |
| Talk to the coordinator remotely | partial: author setup only, not a supported feature | T-818 in review |
| Metrics dashboard, efficiency comparison against another tool | not in the preview; turns and cost stay blank until a finished ticket reports them | T-811 open |
| Native wake for every harness | not in the preview | `docs/wake-recipients.md` |

Known first-run defects, each with the workaround that was tested:

- `atm review` refuses a *sounded* code ticket without `--pr N`, and `--force`
  does not bypass it. `atm plan` sounds everything it writes, so on a repo
  with no pull request to point at, planned work can be claimed but not
  handed back. Use `atm create --deps` on a local board, as above.
- `atm sync` refuses on a repo with no `origin` remote. Skip it on a
  local-only project; `atm review` still pins the branch and commit.
- `atm done` and `atm accept` need the coordinator seat (`atm master take`)
  or the ticket's owner; another seat is refused with a stale-ownership
  message. `atm done` records the commit of the checkout it runs in, so run
  it where the accepted commit is checked out if you want the record to
  match.
- `atm reserve` requires taking master first.

## Plan dependent work

`atm plan` turns JSON `deps` into real `--after` edges. A plan item is
**ready** only when it carries real `cause`, `change` and `proof`; items
without them stay in capture until `tickets sound T-00N`. Plan sounds what it
writes, so a planned code ticket needs `--pr N` at review time; on a repo
with no pull requests use `atm create --deps` instead.

```sh
atm epic create "Auth" --body "..."
atm sprint create "Ship auth" --activate
atm plan <<'EOF'
{"epic":"E-001","sprint":"S-01","tickets":[
 {"key":"A","title":"Task A: write hello.txt","role":"backend",
  "cause":"B needs hello.txt on disk","change":"Write hello.txt","proof":"hello.txt exists"},
 {"key":"B","title":"Task B: consume hello.txt","role":"backend","deps":["A"],
  "cause":"A produced hello.txt","change":"Read and use hello.txt","proof":"consumer sees hello.txt"}
]}
EOF
# tickets plan <<'EOF' ... EOF   (same command under the compatibility alias)
```

After A is done, B is offered by `atm next`. `atm map` shows sprint and epic
progress, `atm graph` diagnoses dependencies, and `atm who` shows live
ownership and worktrees.

## Working as a team

Every harness uses the same small contract: Atman gives it a prompt file, a
working directory and an identity; the harness reports through `atm`.

1. Probe integrations: `atm harness available` (missing is a row).
2. Plan with real `--after` edges (`atm plan` or `atm create --deps`);
   `atm graph` to inspect.
3. Unattended persist to a reviewable SHA on the agent's branch
   (`atm review`).
4. Human review is the gate. Merge is not silent auto-promote.

The same four steps under the alias: `tickets harness available`,
`tickets plan`, `tickets review`, `tickets merge`.

```sh
atm master                   # objective, workforce, reviews, health
atm inbox                    # direct messages and board mentions
atm next                     # claim one ready task atomically
atm update T-012 "..."       # progress and blockers
atm msg "question" --to boss --re T-012
atm review T-012 --notes "paths, tests, decisions"
```

Tasks move through `TO DO → IN PROGRESS → IN REVIEW → DONE`, or `BLOCKED`.
The master seat is replaceable: its objective, decisions, workforce, health
and review queue live on the board, so another seat can take over after a
session ends (`atm master take`). `atm route --claim` assigns by role,
capability, cost and model; `atm merge` tests and integrates submitted
branches.

The built-in runner names are `claude`, `codex`, `cursor`, `cursor+claude`
and `remote` (a fail-closed adapter boundary that never substitutes a local
model). A custom runner is any command:

```sh
atm join qwen --roles docs --harness custom \
  --cmd 'ollama run qwen3:8b < {prompt_file}'
atm harness check qwen
atm spawn qwen --every 3600
```

Message behaviour is chosen per seat with `--wake-mode task-only|continuous`.
Workers default to `task-only`: an explicit task starts them; ordinary
messages wait for their next run.

## Inspectable by design

Coordination lives under the project's `.tickets/` directory:

| Path | Purpose |
| --- | --- |
| `T-001.json` | One file per task, allowing atomic claims without a central server |
| `epics/`, `sprints/` | Delivery structure and active scope |
| `agents/`, `roles.json`, `workforce.json` | Agent identity, capability, cost and availability |
| `messages.jsonl` | Append-only team and direct messages |
| `MASTER.md` | Objective context and durable decision log |
| `briefs/` | Shared, role and agent-specific standing context |

Durable facts live separately under tracked `knowledge/`. Closing a ticket
does not erase its decisions, evidence or runbooks. See the
[handoff contract](docs/handoff-contract.md) and
[board resolution](docs/board-resolution.md).

## Read next

- [First run](docs/onboarding/first-run.md): install to two agents and a recorded verdict, every output shown
- [A first session](docs/first-session.md): the captured quickstart
- [Agent onboarding](docs/onboarding/README.md) and [Master onboarding](docs/onboarding/master-howto.md)
- [Bring your own agent](docs/byoa.md), [Messages and runners](docs/messages-and-runners.md), [Team knowledge](docs/knowledge/README.md)
- [Design notes](docs/design-notes.md), [Recovery contract](docs/recovery-contract.md)
- [Contributing](CONTRIBUTING.md), [Community](docs/community.md), [Code of Conduct](CODE_OF_CONDUCT.md)

Operator and historical notes, not a first read: the author's
[Mac runbook](docs/onboarding/ceo-mac-runbook.md), the
[Atman Core architecture decision](docs/architecture/ADR-001-go-core.md), and
[docs/internal](docs/internal/).

[License](LICENSE) (MIT). Fork, branch off `main`, and open a pull request.
Questions and bug reports go to
[GitHub issues](https://github.com/advitiyavashist/atman/issues).

Built by @abnormal.
