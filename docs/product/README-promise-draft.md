# Atman README: promise and philosophy (copy draft)

Status: T-969 deliverable for T-819. Copy only, in the merged landing voice.
T-819 assembles this around the T-924 demo GIF and the existing install,
worker-loop and master-loop sections of `README.md`. Nothing here adds a
feature, a number, or a claim that is not already on `main`, in
`landing/index.html`, or in the T-940 storyline. Every sentence in sections
1 to 5 has a source in the claims ledger (section 8). If a source changes
before launch, the line changes to match the source, never the other way
round.

Scope bounds from the ticket and the launch cut line (2026-09-15): GitHub
explains and distributes Atman; `atm` launches the local app; one concrete
objective-to-accepted-result path; local, open source, your own
subscriptions. No hosted site, no research path, no native-wake claim, no
metric that has not been reported by a done ticket, no vendor numbers.

---

## 1. Hero

Keep the merged H1. It is the landing headline and the README headline and
should stay the same string in both places.

> # Atman coordinates the agents you already run.

Pain line, one sentence, before any feature (from the storyline, beat 0):

> You finish a task in one agent, then retype it all for the next one.

What it is, one sentence:

> Atman is a local team runtime: one board where Claude Code, Codex, Cursor,
> Antigravity or your own harness take tickets, hand work to each other, and
> show you what needs review.

Supporting line, directly under the sentence:

> Give a coordinator an objective. Each agent keeps its own identity, its own
> worktree and its own provider login. Coordination is plain files on your
> machine, driven by `atm`. You review what comes back before it counts.

Placement note for T-819: the demo GIF goes directly under these four lines,
with its own caption from the storyline (`Your agents. One handoff. No
retyping.`). Do not put a feature list above the GIF.

## 2. Three things it does that nothing else does

**1. One board, separate seats.** Claude Code, Codex, Cursor, Antigravity and
custom harnesses join the same board with their own identity, worktree, role
and cost tier. Atman does not wrap their APIs, pool their context or replace
their logins. Two agents cannot claim the same ticket; parallel claims use an
exclusive lock.

**2. The next task starts on acceptance, with the handoff attached.**
Dependencies are real `--after` edges, so a dependent ticket is invisible to
`atm next` until its predecessor is finished. When you record a verdict on A's
pinned commit and mark it done, B is posted to the next seat and that seat's
prompt carries A's handoff notes. You do not type the next instruction.
Today the seat that receives B starts through a supervised watcher; Atman
labels that as a watcher, not a native wake.

**3. The work outlasts the session.** When a model, session or machine stops,
or a seat hits its usage limit, the claim, the blocker and the next step stay
on the board. A replacement seat inherits the handoff instead of restarting
from chat history. What the board shows is measured, not estimated: turns and
cost per ticket stay blank until a finished ticket reports them. Unknown is
not zero.

## 3. How it feels on day one

Three steps. Each command below is on `main` today.

**Install** (Python 3.9+ and Git; tested on one macOS machine; Homebrew not
published yet):

```sh
git clone https://github.com/advitiyavashist/atman.git
cd atman
./install.sh                 # links atm and tickets into ~/.local/bin
export PATH="$HOME/.local/bin:$PATH"
```

**Onboard** in an existing git repo. One command writes the board, registers
you, and adds three sample tasks in a real dependency chain:

```sh
cd /path/to/your-project
atm quickstart --agent alice --roles backend
atm ui                       # local app: Objective, Team, Work, Intervene
```

`atm connect` walks the tool-specific hook setup for Claude Code, Codex and
Cursor. `atm join <name> --harness custom --cmd '...'` registers anything
else.

**First ticket**, objective to accepted result. This is the path the demo
records, with the same commands:

```sh
atm objective "Add a CSV summary and a command that uses it"
atm plan <<'EOF'
{"tickets":[
 {"key":"A","title":"CSV summary function","role":"backend",
  "cause":"B needs summarize()","change":"Write summarize() and one test",
  "proof":"test passes"},
 {"key":"B","title":"CLI command that calls summarize()","role":"backend",
  "deps":["A"],"cause":"A ships summarize()","change":"Add the command",
  "proof":"command prints the summary"}
]}
EOF
```

1. A seat runs `atm next`, claims A in its own worktree, does the work, and
   hands it back with `atm review T-001 --notes "..."`. Review pins the exact
   branch and commit.
2. You check A and record the verdict against that commit:
   `atm note T-001 "verdict: ACCEPT <branch>@<sha> -- test passes"`, then
   `atm done T-001 --notes "summarize(path) in csv_summary.py"`.
3. Atman unblocks B and posts it to the next seat. That seat claims B, and its
   prompt prints `Handoff from dependencies` with your note from A. Nobody
   retypes what A did.
4. B comes back through `atm review`. You accept it the same way. The Work
   view shows A's verdict, B's handoff, and which commit each verdict is
   bound to.

Total operator keystrokes after accepting A: none, until B is ready for your
review.

## 4. Philosophy

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
(tickets) and durable knowledge (a separate repo-backed graph) are connected
layers, not one store.

**The north star is task completion in the fewest turns.** Atman records
turns and cost per ticket so that routing can improve over time. It reports
only what finished tickets measured. A blank is unmeasured, never a win.

**Local, open source, your own subscriptions.** Coordination is one Python
file and plain files on your machine, MIT licensed. Models run through the
providers and logins you already have. Atman holds no keys, needs no hosted
service, database or agent SDK, and sends nothing anywhere.

## 5. Non-goals

Atman is not:

- a multi-agent framework or a swarm runtime;
- shared memory for agents, or a context store you cannot read;
- one API over Claude, Codex, Cursor and the rest;
- a model router on its own;
- a hosted service, a fleet of cloud machines, or a demo board you log into;
- a compliance, audit or policy-enforcement layer.

Not in the preview, labelled planned in the capability checklist rather than
omitted: Linux and pipx install; Homebrew formula; native wake for every
harness (Cursor and Antigravity start through a supervised watcher; Codex may
stop waking after one run until the watcher restarts); remote control as a
feature; a metrics dashboard; any efficiency comparison against another tool.

## 6. Five-line version for social

> Atman coordinates the agents you already run.
> Claude Code, Codex, Cursor and your own harness on one local board, each with its own identity and worktree.
> Plan A then B. Accept A. B starts on the next seat with A's handoff, without your next command.
> Turns and cost per ticket, blank until a finished ticket reports them.
> Open source, MIT, your own subscriptions: `git clone` and `./install.sh`.

## 7. Words and claims T-819 must not add

- No speed, savings or percentage claim. No vendor throughput or cost figure.
- No "wake", "autonomous", "self-organising", "swarm", "fleet", "scale".
- "Started" or "working" only when a claim or running process is shown;
  otherwise "task posted".
- No "accepted" for a ticket that was only marked done.
- No hosted site, no research program, no metrics dashboard, no remote
  control, no Homebrew install command.
- No harness named as having done work unless its own turn is on screen.

## 8. Claims ledger

| Line | Source on `main` or in the storyline |
| --- | --- |
| H1 "Atman coordinates the agents you already run." | `README.md` H1; `landing/index.html` hero |
| Pain line | T-940 storyline, beat 0 caption |
| Harness list: Claude Code, Codex, Cursor, Antigravity, custom | `tickets.py` `BUILTIN_HARNESSES` includes `agy`; README "Connect a team" |
| Own identity, worktree, provider login | README "Integration" bullet; landing "Keep using your existing provider login" |
| Exclusive lock on claims | README "The worker loop" |
| Dependent ticket invisible until predecessor finished | README "Workflow dependency graph"; landing graph section |
| Handoff notes in the successor prompt | storyline beat 4: `Handoff from dependencies (all notes):` from `tickets.py` `detail()` |
| Verdict recorded with `atm note` against the pinned SHA | storyline section 2 "Acceptance path"; `work_view.py` verdict parsing |
| Supervised watcher, not native wake | README "Known issues"; storyline must-not-imply table |
| Recovery on session end or usage limit | README "Product flow" bullet; landing "Durable handoffs" |
| Turns and cost blank until reported; unknown is not zero | README "Efficiency" bullet; landing efficiency section |
| Install commands, Python 3.9+, Git, one macOS machine, no Homebrew | README "First run", "Preview status" |
| `atm quickstart` writes board plus three chained samples | README "First run"; `docs/first-session.md` |
| `atm connect` tool-specific onboarding | README "Connect a team" |
| Seat = intelligence + context + boundary + capability | README "What Atman manages" table |
| cause / change / proof gate on plan items | README "Plan dependent work" |
| Review queue, no silent auto-promote | README "What works today"; landing "Intervene" |
| Explicit text context, no hidden shared memory | README "What Atman manages"; "Context without repetition" |
| Tickets and knowledge are separate layers | README "Context without repetition"; launch plan philosophy paragraph |
| Fewest turns north star | `docs/onboarding/README.md`; E-011 product direction |
| One Python file, standard library, MIT, no hosted service | README "What works today"; landing "No hosted service, MIT licensed" |
| Non-goals list | README subtitle; E-011 boundaries; storyline must-not-imply table |
| Planned list | CEO launch cut line 2026-09-15; README "Known issues" |
