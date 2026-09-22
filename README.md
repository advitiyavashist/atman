<p align="center">
  <img src="docs/brand/assets/lockup.svg" width="176" alt="atman">
</p>

<p align="center"><b>One Claude Code session does one task at a time, hits its limit mid-task, and forgets the plan by morning. Atman runs your coding agents as a team, on a board in your own repo — where no task starts until another agent has checked the last one.</b></p>

<p align="center">
  <img src="docs/assets/demo/hero.gif" width="790" alt="Two agents finishing a dependency chain on an Atman board">
</p>

<p align="center"><a href="https://advitiyavashist.github.io/atman/demo.html">Two-minute deck</a>: one session versus a team, animated. Arrow keys or swipe.</p>

## Why

You give Claude Code a chunk of work. It does the schema, then the API, then
the UI, on Opus, one at a time. Open a second session to go faster and you
become the copy-paste bus between them; each one knows half. Somewhere in the
middle the usage limit lands, and the half-done branch and the reasoning
behind it sit in a chat window. Meanwhile the Codex and Cursor subscriptions
you also pay for do nothing.

Atman makes that session the lead. It writes the tickets with real
dependencies, other seats claim them in parallel, and every decision, handoff
and message lives on the board in your repo. A ticket that stops mid-way is
reopened with its notes and branch for the next seat that claims it
(same-provider recovery is tested; see the [table](#preview-status-and-limitations)).
You put the expensive model where the hard decisions are and the cheap seat
gets the README typo. Nothing lives in a chat window.

## What it does

You give one agent an objective. It writes tickets with real dependencies. Agents
claim them, and each hands work back as a **commit**, not a summary.

Then the part that makes it a team instead of a queue:

1. A worker finishes A and submits it, pinned to an exact commit.
2. **A different agent** — never the author — checks that commit and accepts it.
3. Only then does B open, and B's agent is handed A's accepted commit.

So the next task starts from work someone verified, and a "done" nobody checked
stops the chain instead of quietly feeding the next agent.

Claude Code, Codex and Cursor join the same board, each with its own identity,
worktree and provider login. Atman is a CLI (`atm`) plus a local app
(`atm ui`): the board is plain files under `.tickets/` in your repository —
greppable, diffable, reviewable in a pull request. No hosted service, no
database, no shared memory, and nothing leaves your machine except your agents
talking to their own providers.

<p align="center">
  <img src="landing/assets/t971-app-work-1440.png" width="920" alt="The Atman app Work view: objective, dependency graph, and cost left blank when unmeasured">
</p>

<p align="center"><i>A checked-in capture of the local app. There is no hosted board to log into.</i></p>

## One path

Python 3.9+ and Git. Tested on one macOS machine:
`git clone` plus `./install.sh`.

```sh
git clone https://github.com/advitiyavashist/atman.git
cd atman
./install.sh                 # links atm and tickets into ~/.local/bin
export PATH="$HOME/.local/bin:$PATH"
atm self                     # which file you are actually running
```

`install.sh` refuses to overwrite an `atm` or `tickets` it does not own.
`./install.sh --prefix DIR` places the symlinks in `DIR`; they still run
`tickets.py` from this checkout. `--force` replaces the existing one on
purpose. `atm` is the command; `tickets` is a compatibility alias.

Or Homebrew on macOS, which installs the current tagged release instead of a
checkout:

```sh
brew tap advitiyavashist/tap
brew install atman
atm --version                # tickets commit b34d423cac00266a2cc6fc23b94759ee04894f71 (verified release)
```

The formula `depends_on "python@3.13"`, so Homebrew installs and uses its own
Python 3.13 even if you already have the Python 3.9+ the clone path needs.

There is no PyPI package. `pip install atm` installs an unrelated project that
happens to share the name; it is not Atman.

## Feel it in five minutes

```sh
atm quickstart --gate
```

One command, nothing of yours touched: it makes a throwaway git repo and board
in a temp dir, dispatches one seat to make a one-line commit, and then lets you
watch a dependent ticket refuse to open until a **different** seat accepts that
exact commit. Real output from a run:

```
atm quickstart --gate: one ticket, two seats, and the gate between them.
scratch: /tmp/atm-quickstart-gate-5mvvgob2
         (your repo and your board are not touched -- this all happens in there)
1/8 preflight claude login is live (ready) -- same check `atm dispatch` makes
2/8 repo      git init + first commit on main (<scratch>/repo)
3/8 board     atm init -> <scratch>/repo/.tickets (a board of its own)
4/8 seats     alice (author) and bob (reviewer) joined, harness=claude
5/8 tickets   T-001 (the work) and T-002 (waits for T-001)
6/8 dispatch  T-001 reserved for alice and claimed; running claude now...
          | claude made the commit
          | alice committed bcda9a0
7/8 gate      T-002 blocked: T-001 has no accept from another seat
          | blocked: T-002 -- T-001 marked done without verification; accept it or reopen
          | atm claim T-002 (as bob) refused: T-002: T-001 marked done without verification; accept it or reopen
8/8 released  T-002 released by bob accepting bcda9a0
          | author alice != evaluator bob  (the board record, not a claim)
          | T-001 accepted bcda9a086e3de3ca390e55fa51dc6703c4c6353d by bob
          | unblocked: T-002
          | started: T-002
usage:  claude unknown · UNKNOWN · last read 0s ago · re-login required

what happened: alice committed bcda9a0 and closed T-001; T-002 stayed shut until bob --
               a different seat -- accepted that exact sha. No seat releases its own work.
in your repo:  cd <your repo> && atm quickstart --agent <you>   (then: atm next, atm review, atm accept)
where to look: atm agents   (seats, harness, usage)   |   atm ui
scratch kept:  /tmp/atm-quickstart-gate-5mvvgob2   (rm -rf it whenever; nothing of yours was touched)
```

The lines under `7/8` and `8/8` are the CLI's own: `atm done` refuses to release
T-002, `atm claim T-002` is refused for the same reason, and the release only
happens once `bob` records an accept bound to `bcda9a0`. The demo verifies the
accept it printed against the board record (author != evaluator) and stops
loudly if the gate ever fails to hold, so the moment cannot be a print
statement. In this capture the dispatched CLI was a one-line stub that makes the
commit, so the paste is reproducible; with a real Claude/Codex/Cursor login the
same run dispatches that CLI instead.

If no coding CLI is logged in, the same command says so, prints the login
command, and narrates a `WALKTHROUGH ONLY` version that writes no board, records
no accept and invents no sha (`atm quickstart --dry-run` asks for that on
purpose). Then, in your own repo:

```sh
cd <your repo> && atm quickstart --agent <you>   # board + sample work + you, registered
```

The sample tickets are T-001 through T-003. Remove them before you create your
own (the walkthrough below does), or a `--deps T-001` of yours waits on a
sample.

Tell us where you got stuck: `atm feedback` prints a local-only, pasteable
run summary (nothing leaves this machine) for a
[friction report](https://github.com/advitiyavashist/atman/issues/new?template=friction-report.yml).

In an existing git repo:

```sh
cd /path/to/your-project     # an existing git repo (git init if not)
unset TICKETS_DIR            # a stale export wins over everything
atm where                    # the board path atm will use
atm quickstart --agent boss --roles backend
atm ui                       # local app: Objective, Team, Work, Intervene (Ctrl-C to stop)
atm quickstart --remove      # drop sample T-001..T-003; your first ticket is T-001
```

`quickstart` writes `AGENTS.md`, `.gitignore` and `.cursor/rules/tickets.mdc`
without committing them. Two seats in your own shell: `boss` accepts,
`worker` does the work.

```sh
export TICKET_AGENT=boss
atm master take
atm objective "Add a CSV summary and a command that uses it" \
  --exit "summary command prints row and column counts"
atm create "CSV summary function" --role backend \
  --body "Cause: B needs summarize(). Change: write summarize(path) and one test. Proof: test passes."
atm create "CLI command that calls summarize()" --role backend --deps T-001 \
  --body "Cause: A ships summarize(). Change: add the command. Proof: command prints the summary."
atm graph                    # T-002 (backend; waiting on T-001)
```

If `atm graph` ever lists `broken references` (a dependency on a ticket that
no longer exists, such as a removed sample), the repair is
`atm dep <id> --drop <missing>`, plus `--after <id>` to re-point it. A
checkout of `main` prints that line under each broken reference; the v0.3.0
release does not yet.

Dependencies are real `--after` edges (`atm create --deps` or `atm plan`).
`atm review` pins a reviewable SHA. Human review is `atm accept --sha`
from a seat that is not the author. `atm harness available` lists each
harness row.

1. Worker claims A, commits, and hands it back.

   ```sh
   export TICKET_AGENT=worker
   atm join worker --roles backend
   git worktree add .worktrees/worker -b worker && cd .worktrees/worker
   atm next                  # [>] IN PROGRESS T-001 ... owner=worker
   # ... write csv_summary.py and its test, commit ...
   atm review T-001 --notes "csv_summary.py summarize(path); test passes"
   # pinned worker@76279da in <repo>/.git (this checkout -- ...)
   ```

2. Boss accepts that exact commit, then marks A done. `atm accept` refuses
   a short SHA, a SHA that is not the submitted review head, and the
   ticket's author. Done without that accept leaves T-002 closed.

   ```sh
   export TICKET_AGENT=boss
   atm accept T-001 --sha <full 40-character SHA> --notes "ran the test: passes"
   # T-001 accepted 76279dac... by boss
   atm done T-001 --artifact /path/to/project/.worktrees/worker \
     --notes "summarize(path) -> dict in csv_summary.py"
   # T-001 done ... recorded worker@76279da ... unblocked: T-002
   ```

3. T-002 is now visible to `atm next`. The worker's prompt carries A's
   handoff. Nobody retypes it.

   ```sh
   export TICKET_AGENT=worker
   atm next
   # [>] IN PROGRESS T-002  CLI command that calls summarize()  (role=backend; owner=worker; after T-001)
   # Handoff from dependencies (all notes):
   #   T-001 (CSV summary function): worker@76279da -- summarize(path) -> dict in csv_summary.py
   #   T-001 (CSV summary function): ran the test: passes
   ```

Claude Code, Codex and Cursor join the same board with their own identity,
worktree and provider login. Cursor starts through a supervised watcher,
not a native wake. Codex may stop waking after one run; restart the
watcher. A custom harness is any command whose template names
`{prompt_file}` ([Bring your own agent](docs/byoa.md)).

## Links

[GitHub issues](https://github.com/advitiyavashist/atman/issues)
·
[Site](https://advitiyavashist.github.io/atman/)
·
[Deck](https://advitiyavashist.github.io/atman/demo.html)
·
[A first session](docs/first-session.md)
·
[Agent onboarding](docs/onboarding/README.md)
·
[Master how-to](docs/onboarding/master-howto.md)
·
[Preview status](#preview-status-and-limitations)

## Preview status and limitations

This is an early developer preview, tested on one macOS machine with Claude
Code, Codex and Cursor. This table records what is tested, partial, and
planned for this preview. Its evidence links point to files and tests in this
repository.

| Claim | Status on 2026-09-15 | Evidence |
| --- | --- | --- |
| Install: `git clone` + `./install.sh` | tested, one macOS machine | `tests/test_live_install.py`; walkthrough above |
| Install: Homebrew on macOS (`brew tap advitiyavashist/tap && brew install atman`) | published: installs the current release, `v0.3.0`, and brings its own Python 3.13 via `depends_on "python@3.13"` | tap `advitiyavashist/homebrew-tap`, `Formula/atman.rb`; release `v0.3.0` tarball sha256 matches the formula; `packaging/homebrew/atman.rb` |
| Install: PyPI | none: there is no PyPI package, and `pip install atm` installs an unrelated project | no upload from this repository |
| Install: Linux packages, pipx | planned | no artifact in this repository yet |
| Install: `pip install -e .` console scripts | not a supported preview path: the packaged `atm`/`tickets` is the smaller core-board CLI without `watch`, `spawn`, `hooks`, `ui` or `remote` | `pyproject.toml`; `src/ticket_board/cli.py` |
| Claude Code seat takes tickets and reports back | tested on one macOS machine; public tests cover the hook, poke and wake path | `tests/test_t785_t789_all_provider_wake.py`; `tests/test_t857_claude_uds.py` |
| Codex seat takes tickets | partial: may stop waking after one run; restart the watcher | known issue since the first preview |
| Cursor seat starts through a supervised watcher | partial: supervised, not a native wake | `docs/wake-recipients.md` |
| Antigravity seat | experimental: discovery is documented; headless launch and task completion are not supported in this preview | `docs/connect-agy.md`; `docs/wake-recipients.md` |
| Custom harness via `atm join --harness custom --cmd` | documented contract; smoke test pending | `docs/byoa.md` |
| Dependent ticket stays closed until its predecessor is accepted on the full review SHA by a seat other than the author; handoff notes then appear in the successor prompt | tested | `tests/test_t1031_accept_gate.py`; `tests/test_t944_accept_reject.py`; `tests/test_t780_plan_graph.py`; walkthrough above |
| Acceptance bound to the full review SHA; author cannot accept own work | tested | `tests/test_t944_accept_reject.py`, `tests/test_t889_work_view.py` |
| Recovery after a seat stops | same-provider restart from the persisted handoff and ownership lease; cross-provider continuation not proven | `docs/recovery-contract.md` |
| Talk to the coordinator remotely | not supported in the developer preview | `remote` runner contract in `docs/messages-and-runners.md` |
| Local app: `atm ui` | in the preview; the board stays on this machine; no hosted board | walkthrough above |
| Local metrics view: turns and cost per finished ticket | in the preview; values stay blank until a finished ticket reports them; `atm turns` estimates are labelled list price | `src/ticket_board/turns.py`; `tests/test_t480_cost_estimate.py` |
| Claude usage remaining (`atm who`) | tested on this macOS machine via the login keychain (service `Claude Code-credentials`) first; `~/.claude/.credentials.json` is a fallback and is absent on macOS Claude Code. A failed or absent read stays unknown (re-login required), never a fabricated remaining | `tests/test_t1056_claude_keychain.py`; `src/ticket_board/provider_usage.py` |
| Efficiency comparison against another tool | not in the preview | none |
| Native wake for every harness | not in the preview | `docs/wake-recipients.md` |

Known first-run defects, each with the workaround that was tested:

- `atm review` refuses a *sounded* code ticket without `--pr N`, and `--force`
  does not bypass it. `atm plan` sounds everything it writes, so on a repo
  with no pull request to point at, planned work can be claimed but not
  handed back. Use `atm create --deps` on a local board, as above.
- `atm sync` refuses on a repo with no `origin` remote. Skip it on a
  local-only project; `atm review` still pins the branch and commit.
- `atm accept` requires a reviewer other than the ticket's author; it does
  not require the master seat. `atm done` on a ticket still in progress
  requires its current owner; once the ticket is in review any seat can close
  it. With an accept present, `atm done` records that accepted commit, not
  the closer's HEAD; use `--artifact` to point it at the accepted worktree
  when closing elsewhere.
- `atm reserve` requires taking master first.

## Read next

- [A first session](docs/first-session.md)
- [Agent onboarding](docs/onboarding/README.md) and [Master onboarding](docs/onboarding/master-howto.md)
- [Bring your own agent](docs/byoa.md), [Messages and runners](docs/messages-and-runners.md), [Team knowledge](docs/knowledge/README.md)
- [Recovery contract](docs/recovery-contract.md)
- [Contributing](CONTRIBUTING.md), [Community](docs/community.md), [Code of Conduct](CODE_OF_CONDUCT.md)

Operator and historical notes, not a first read: the author's
[Mac runbook](docs/onboarding/ceo-mac-runbook.md) and the
[Atman Core architecture decision](docs/architecture/ADR-001-go-core.md).

[License](LICENSE) (MIT). Fork, branch off `main`, and open a pull request.
Questions and bug reports go to
[GitHub issues](https://github.com/advitiyavashist/atman/issues).
