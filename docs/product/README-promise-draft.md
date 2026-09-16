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

Revision 2 (2026-09-15, CEO review of f20d7d4): acceptance is `atm accept`
bound to the full review SHA, not a prose note; two official install paths
with today's Homebrew status stated; Antigravity named with Cursor as a
supervised seat and given an evidence row; privacy line split into what stays
local and what your agents still send; the keystroke claim replaced by "no
further instruction from you" with the watcher caveat. Section 9 lists the
capability-checklist rows (T-975) this copy depends on.

Revision 4 (2026-09-15, CEO final word on Antigravity, after the live
probe): Antigravity is experimental in the preview, not supported. It leaves
the hero sentence; Claude Code, Codex, Cursor and custom harnesses stay. The
board shows Agy seats delivered tickets in earlier runs, but `agy -p` on the
author's machine returned only `[agy] print timeout after 5m0s with turn in
progress` for a one-line prompt, and the watcher allows 90 seconds per run,
so an Agy seat cannot deliver today. Tracked in T-988. Checklist row: discovery
passes, launch fails headless, task completion is historical only.

---

## 1. Hero

Keep the merged H1. It is the landing headline and the README headline and
should stay the same string in both places.

> # Atman coordinates the agents you already run.

Pain line, one sentence, before any feature (from the storyline, beat 0):

> You finish a task in one agent, then retype it all for the next one.

What it is, one sentence:

> Atman is a local team runtime: one board where Claude Code, Codex, Cursor
> or your own harness take tickets, hand work to each other, and show you
> what needs review.

Supporting line, directly under the sentence:

> Give a coordinator an objective. Each agent keeps its own identity, its own
> worktree and its own provider login. Coordination is plain files on your
> machine, driven by `atm`. You review what comes back before it counts.
> Cursor seats start through a supervised watcher.

Placement note for T-819: the demo GIF goes directly under these four lines,
with its own caption from the storyline (`Your agents. One handoff. No
retyping.`). Do not put a feature list above the GIF.

## 2. Three things it does that nothing else does

**1. One board, separate seats.** Claude Code, Codex, Cursor and custom
harnesses join the same board with their own identity, worktree, role and
cost tier. Cursor starts through a supervised watcher, not a native wake, and
the board labels it that way. Antigravity is experimental: it has joined the
board and delivered tickets in earlier runs, but its headless one-shot mode
does not return reliably on the author's machine today (T-988). Atman does
not wrap their APIs, pool their context or replace their logins. Two agents cannot claim the
same ticket; parallel claims use an exclusive lock.

**2. The next task starts on acceptance, with the handoff attached.**
Dependencies are real `--after` edges, so a dependent ticket is invisible to
`atm next` until its predecessor is finished. When you accept A on its exact
review commit and mark it done, B is posted to the next seat and that seat's
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

Three steps. Every `atm` command below is on `main` today.

**Install.** Two official paths: Homebrew on macOS, and `git clone` plus
`./install.sh` anywhere with Python 3.9+ and Git. As of 2026-09-15 the
Homebrew formula is in the repository but the tap is not yet published
(T-898), so the command that works today is the clone. The clone path is
tested on one macOS machine; the formula's test block is proven against the
built release tarball, not yet through a live `brew install`. Linux and pipx
are planned.

```sh
git clone https://github.com/advitiyavashist/atman.git
cd atman
./install.sh                 # links atm and tickets into ~/.local/bin
export PATH="$HOME/.local/bin:$PATH"
```

macOS, once the tap is published (T-819 checks T-898 on the day and keeps or
removes the "not yet published" label):

```sh
brew install advitiyavashist/homebrew-tap/atman
atm --version                # verified release, same check as atm self
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
   branch and commit (`recorded <branch>@<sha7>`).
2. You check A and accept it on that exact commit, from your own seat. Take
   the full 40-character SHA from the reviewed worktree (`git rev-parse
   <branch>`) or from the PR head:

   ```sh
   atm accept T-001 --sha <full 40-character SHA of that commit> --notes "test passes"
   atm done T-001 --notes "summarize(path) in csv_summary.py"
   ```

   Acceptance is bound to that commit. `atm accept` refuses a short SHA, a SHA
   that is not the submitted review head, and a reviewer who is the ticket's
   author. A note that merely says "accept" is not a verdict: the Work view
   shows such a ticket as `Marked done; verification not recorded`.
3. Atman unblocks B and posts it to the next seat. That seat claims B, and its
   prompt prints `Handoff from dependencies` with your note from A. Nobody
   retypes what A did.
4. B comes back through `atm review`. You accept it the same way. The Work
   view shows `Accepted by @you on <sha7>` for A, B's handoff, and which
   commit each verdict is bound to.

No further instruction from you after accepting A: the next seat's supervised
watcher polls the board, claims B and starts it with A's handoff attached.
The watcher's own `atm next` call is the mechanism, and the board labels it a
watcher, not a native wake.

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
file and plain files on your machine, MIT licensed. Atman's coordination
state stays on your machine, and Atman holds no keys, hosted service,
database or agent SDK. Your agents still send their prompts to their own
providers under your logins, exactly as they do without Atman.

## 5. Non-goals

Atman is not:

- a multi-agent framework or a swarm runtime;
- shared memory for agents, or a context store you cannot read;
- one API over Claude, Codex, Cursor and the rest;
- a model router on its own;
- a hosted service, a fleet of cloud machines, or a demo board you log into;
- a compliance, audit or policy-enforcement layer.

Not in the preview, labelled planned in the capability checklist rather than
omitted: Linux and pipx install; the published Homebrew tap (the formula is in
the repository); native wake for every harness (Cursor starts through a
supervised watcher; Codex may stop waking after one run until the watcher
restarts); an Antigravity seat that delivers today (experimental: headless
one-shot mode does not return on the author's machine, T-988); remote control
as a feature; a metrics dashboard; any
efficiency comparison against another tool.

## 6. Five-line version for social

> Atman coordinates the agents you already run.
> Claude Code, Codex, Cursor and your own harness on one local board, each with its own identity and worktree.
> Plan A then B. Accept A on its exact commit. B starts on the next seat with A's handoff, with no further instruction from you.
> Turns and cost per ticket, blank until a finished ticket reports them.
> Open source, MIT, your own subscriptions: `git clone` and `./install.sh`.

## 7. Words and claims T-819 must not add

- No speed, savings or percentage claim. No vendor throughput or cost figure.
- No "wake", "autonomous", "self-organising", "swarm", "fleet", "scale".
- "Started" or "working" only when a claim or running process is shown;
  otherwise "task posted".
- No "accepted" for a ticket that was only marked done.
- No hosted site, no research program, no metrics dashboard, no remote
  control. The Homebrew command appears only with its publication status
  (T-898) stated next to it on launch day.
- No "accepted" without an `atm accept` event bound to the full review SHA;
  a prose note is `verification not recorded`.
- No "no keystrokes" or "zero operator input" wording; the claim is "no
  further instruction from you", with the watcher named.
- No harness named as having done work unless its own turn is on screen.
- No "supported" or "works" for Antigravity; the word is "experimental",
  and it stays out of the hero sentence and the social version.

## 8. Claims ledger

| Line | Source on `main` or in the storyline |
| --- | --- |
| H1 "Atman coordinates the agents you already run." | `README.md` H1; `landing/index.html` hero |
| Pain line | T-940 storyline, beat 0 caption |
| Harness list: Claude Code, Codex, Cursor, custom | `tickets.py` `BUILTIN_HARNESSES`; README "Connect a team" |
| Cursor starts through a supervised watcher | README "Known issues"; `docs/wake-recipients.md` |
| Antigravity is experimental: delivered tickets in earlier runs, headless mode does not return today | Review pins by Agy seats: T-811 `agy-aira2-tty-work@04f7c4a`, T-869 `atman-pmm-agy-0913@3679053`, T-870 `steer-pmm-agy-0913@f9d9ee2`; CEO probe 2026-09-15: `agy -p` returned only `[agy] print timeout after 5m0s with turn in progress`, watcher cap 90 s per run; T-988 (adapter ticket); `docs/connect-agy.md`; `docs/wake-recipients.md` (`agy 1.2.2`, supervised) |
| Own identity, worktree, provider login | README "Integration" bullet; landing "Keep using your existing provider login" |
| Exclusive lock on claims | README "The worker loop" |
| Dependent ticket invisible until predecessor finished | README "Workflow dependency graph"; landing graph section |
| Handoff notes in the successor prompt | storyline beat 4: `Handoff from dependencies (all notes):` from `tickets.py` `detail()` |
| Acceptance is `atm accept --sha <full 40> --notes`, bound to the submitted review head, author cannot accept own work | `tickets.py` `cmd_accept`; `src/ticket_board/review_verdict.py` `refuse()` (T-944); `work_view.py` label `Marked done; verification not recorded` for prose notes |
| Supervised watcher, not native wake | README "Known issues"; storyline must-not-imply table |
| Recovery on session end or usage limit | README "Product flow" bullet; landing "Durable handoffs" |
| Turns and cost blank until reported; unknown is not zero | README "Efficiency" bullet; landing efficiency section |
| Two official install paths; Homebrew tap not yet published on 2026-09-15 | Operator decision 2026-09-15 (CEO message on T-969); README "Preview status" and "macOS (Homebrew)"; `packaging/homebrew/atman.rb` (T-865 done); T-898 open; `advitiyavashist/homebrew-tap` does not resolve on GitHub today |
| Linux and pipx planned | Launch cut line 2026-09-15 (T-866) |
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
| Coordination state local, no keys; agents still call their providers | README "What works today"; landing "Keep using your existing provider login"; `docs/product/agent-onboarding-and-subscription-usage.md` (no credential printed, no private endpoint called) |
| "No further instruction from you", watcher caveat | T-940 storyline beat 4 (`supervised watcher`, "nothing typed by the operator in any pane"); README "Known issues" |

## 9. Capability-checklist rows this copy depends on (for T-975)

Every path and harness named above needs a row in
`docs/launch/capability-checklist.md`. Proposed rows, with the evidence that
exists on 2026-09-15; T-975's owner decides the final status word.

| Claim | Claimed in | Tested by | Status |
| --- | --- | --- | --- |
| Install: `git clone` + `./install.sh` | README, this draft | `tests/test_live_install.py`; T-972 proof run on throwaway boards from an isolated prefix (PR #171) | tested |
| Install: Homebrew on macOS | README, this draft | `tests/test_t865_homebrew_release.py` (formula test-block operations proven against the built tarball, PR #120 @ffbaf46); live `brew install` not proven; tap not published (T-898) | partial: formula in repo, tap unpublished |
| Install: Linux, pipx | this draft (planned list) | none | planned (T-866) |
| Harness: Claude Code seat takes tickets | README, this draft | T-924 demo take (CEO pane); board history | tested |
| Harness: Codex seat takes tickets | README, this draft | T-924 demo take (worker pane); README known issue: may stop waking after one run | partial: watcher restart caveat |
| Harness: Cursor seat starts B through a supervised watcher | README, this draft, storyline beat 4 | T-924 demo take; `docs/wake-recipients.md` | partial: supervised, not native |
| Harness: Antigravity (experimental). Discovery: `agy` binary on PATH (`atm harness available`). Launch: FAILS headless; `agy -p` returned only `[agy] print timeout after 5m0s with turn in progress` for a one-line prompt on 2026-09-15, against a watcher cap of 90 s per run (evidence in T-988). Task completion: historical only, from earlier runs; review pins T-811 (`agy-aira2-tty-work@04f7c4a`), T-869 (`atman-pmm-agy-0913@3679053`), T-870 (`steer-pmm-agy-0913@f9d9ee2`); done tickets owned by Agy seats T-754, T-775, T-797, T-801, T-806. T-823, T-890 and T-900 were named as Agy-delivered, but the board records T-823 pinned by a Codex seat, T-890's Agy run stopping on a quota limit with a Cursor takeover, and T-900's Agy launch rejected, so they are not cited. Native wake: unsupported | this draft, `docs/connect-agy.md` | T-988; `docs/wake-recipients.md`; the review pins listed | experimental: discovery passes, headless launch fails, completion historical |
| Harness: custom via `atm join --harness custom --cmd` | README, this draft | `docs/byoa.md` | needs a test id from T-975 |
| Acceptance bound to the full review SHA; author cannot accept own work | this draft | `tests/test_t944_accept_reject.py`, `tests/test_t889_work_view.py` | tested |
