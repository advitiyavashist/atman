# Atman landing demo: storyline and on-screen script

Status: T-940 deliverable for the T-924 hero recording. Doc only. Revised for
Sol's T-941 honesty review (`docs/reviews/t941-t940-c564fa4.md` on
`review/sol-t940-t941`) and the CEO tightenings on T-940. Sol reviews for
honesty; CEO approves before anything is recorded.

This is a **proposed take, informed by the CEO dry run of 2026-09-14 12:15**.
The dry run proved planning, a claim, and the posting of the success-trigger
message. It did not prove agent inference, watcher execution, or the Work view
beats. Every string quoted below in `code` is what `tickets.py` and
`src/ticket_board/work_view.py` at 786be1f print or render; the recording seat
re-checks each one on the merged release before recording. If the run prints
something else, the caption changes to match the run, never the other way
round.

Editing rules carried in from the earlier storyboard so nothing depends on a
file outside this tree: no fake output, no spliced runs, no hidden manual
resume; original timestamps stay in the cast; a `waits shortened` label appears
wherever idle time was compressed; the uncut cast is linked; native session
wake versus supervised launch is labeled by what was actually received and
executed, never by a pane title.

## 1. The viewer, the pain, the promise

**Viewer.** An engineer who already runs Claude Code, Codex or Cursor, often
two of them in the same week. They lose time in two places: re-explaining the
same context to the next agent, and hand-carrying "A is done, now do B" between
terminals.

**Pain to open on (first 5 seconds, no jargon).** One line, on screen before
any command runs:

> You finish a task in one agent, then retype it all for the next one.

**The one-sentence promise the demo proves.**

> After you accept A, Atman gives B to the next agent with A's handoff, without
> your next command.

That is the whole claim. "Accept" means a recorded verdict on A's pinned
artifact, not a done flag (section 2). "Without your next command" is scoped to
the operator: the operator types nothing after accepting A; agent code may call
`atm next`. The recording either shows all of it or the demo does not ship.
Everything else Atman does (liveness, limits, cost, review queue) stays out of
the hero.

## 2. The proposed run behind the beats

Three seats on a throwaway repo and throwaway board, one real agent process per
pane:

| Seat | Harness | Pane title | Does |
| --- | --- | --- | --- |
| `ceo` (master) | Claude Code | `Claude Code (CEO)` | joins, sets objective, `atm plan` A then B, records the ACCEPT verdict on A, `atm done T-001` |
| `codex-worker` | Codex | `Codex (worker)` | `atm next` claims A in its own worktree, works, `atm review` |
| `cursor-worker` | Cursor | `Cursor (worker)` | sits behind `atm watch`; receives B's task, claims, starts |

**Attribution rule.** A caption names Claude, Codex or Cursor only if that
agent's own inference or actions are on screen: its prompt turn, its tool
calls, its edits. A shell script running `atm plan` in a pane titled Claude
does not prove Claude planned it. If a beat only shows board output, the
caption names the board action (section 3 fallbacks).

**Task shape (CEO tightening 1).** Task A is sized so real Codex work finishes
in under about 90 seconds: one small CSV summary function plus one test. Task B
adds a command that calls A's function. B's `proof` names A's interface. If A
still runs long, the highlight keeps the test pass line and line 2 says
`waits shortened`; output is never trimmed.

**Acceptance path.** There is no `atm accept` command. The Work view derives a
verdict from a note or message whose text starts with `accept` / `approved` /
`lgtm` or contains `verdict`, and binds it to the artifact by a SHA in that text
matching the pin that `atm review` recorded (`pinned <branch>@<sha> in ...`).
So the CEO seat records acceptance as:

```
atm note T-001 "verdict: ACCEPT <branch>@<sha> -- <what was checked>"
atm done T-001 --notes "<A's interface: function name, signature, module path>"
```

`atm done` alone marks done and posts successor work; the Work view labels a
done ticket with no verdict `Marked done; verification not recorded`. The
verdict note must precede it and must quote the pinned SHA, or beat 3 cannot say
"accepted". If the CEO integrates with `atm merge` instead, the note
`merged into main as <sha> (tickets merge; pinned <sha>)` is written and the Work
view label becomes `Merged into main as <sha7>`; that label is acceptable in
beat 5 and the caption does not need `Accepted by`.

**Why the done note carries A's interface.** The Work view's `Handoff` row on
B shows the **last note on each finished dependency**, not the author's review
note. After `atm done` the last note on A is the CEO's done note, prefixed with
`<branch>@<sha> --`. Put the interface there, or the Handoff row shows a bare
pin.

**Preconditions before recording:**

1. PR #147 @eb5e2b6 (T-883, successor to stale #124: wake only seats enrolled
   on this board) must be merged after non-author verification. Without it
   `atm done` prints `wake: cursor -> inbox-posted` and `wake: atman-ceo ->
   inbox-posted` for seats that never joined the throwaway board, and the
   recording shows ghost seats.
2. The merged release is checked for the enrolled-seat fix and for T-939
   identity isolation before the first take. Global Cursor, Codex and Claude
   hooks that pin an identity (T-856, T-873, T-939) are off the recording
   machine; the operator moves `~/.cursor/hooks.json` aside. Seats get
   worktree-scoped hooks only.
3. `atm done` prints `started: T-002 -> cursor-worker`. That line means a task
   message was posted to the seat, not that work began. The caption over that
   line says **task posted**, never "started" or "working".
4. `atm reserve` needs `atm master take` first even after `join --roles
   master`. Do it before recording starts; it is setup, not story.
5. `atm done` refuses to run from the main checkout. The CEO pane runs from its
   own worktree. This is a feature and can stay visible if it happens; do not
   cut a refusal and retry it off camera.
6. The Cursor watcher runs with a short poll (`atm watch --every 5 --cwd
   <cursor worktree> ...`) so the uncompressed handoff in beat 4 fits the
   budget without any editing.

## 3. Beats, captions, evidence

Caption bar: a 2-line pane at the top of the tmux layout, written by
`demo.sh` at each beat (CEO 12:40 addition). Line 1 is the caption, at most 8
words, plain. Line 2 is a status label that stays true for the whole beat.
Narration only; the bar never mimics tool output. `waits shortened` sits on
line 2 whenever idle time was compressed in that beat.

| # | Time | Pane in focus | Caption (line 1, ≤ 8 words) | Line 2 | What must be visible on screen for the caption to be true |
| --- | --- | --- | --- | --- | --- |
| 0 | 0–5s | full layout, no zoom | **Stop retyping context between your agents.** | `Claude Code · Codex · Cursor` | three titled panes, all idle, no board state yet. An invitation, not a savings claim |
| 1 | 5–15s | Claude pane | **Claude plans A, then B.** | `atm plan · real cause / change / proof` | Claude Code's own turn authoring or running `atm plan` with `cause`, `change`, `proof` for both items and `deps: ["A"]` on B; the result showing T-001 ready and T-002 waiting on T-001 |
| 2 | 15–25s | Codex pane | **Codex claims A in its own worktree.** | `atm next · worktree .worktrees/codex-worker` | the `atm next` claim of T-001; a worktree receipt (the `atm who` row for `codex-worker` with its `worktree` column, or Codex's own `pwd`), not a pane title; Codex's own edits and test run passing; `atm review T-001 --notes ...` printing `pinned <branch>@<sha> ...` then `T-001 -> IN REVIEW after ...` |
| 3 | 25–35s | Claude pane | **A accepted. B's task posted to Cursor.** | `verdict on <sha7> · atm done · success trigger` | the verdict note command quoting the pinned SHA; then `atm done T-001` output: `T-001 done in ...`, `recorded <branch>@<sha>`, `unblocked: T-002`, `started: T-002 -> cursor-worker` |
| 4 | 35–47s | Cursor pane, zoomed | **Cursor starts B without your next command.** | `supervised watcher · not a native wake · real time` | the earlier `watching <board> for cursor-worker every 5s; wake=...; cwd=...; cmd=...` line; the inbox line `unblocked T-002 after T-001 -- start (success trigger)`; the watcher launching Cursor; Cursor's claim of T-002 whose detail prints `Handoff from dependencies (all notes):` followed by A's done note; nothing typed by the operator in any pane |
| 5 | 47–55s | app Work view, two node selections | **A's review. B's handoff.** | `Work view · captured app frame` | frame 1: T-001 selected, `Review` row `Accepted by @ceo on <sha7>` (or `Merged into main as <sha7>`); frame 2: T-002 selected, phase `Working` (or `Task posted`), `Handoff` row showing `T-001` and the CEO's done note with A's interface |
| 6 | 55–60s | caption bar over the full layout | **Your agents. One handoff. No retyping.** | `git clone https://github.com/advitiyavashist/atman.git && cd atman && ./install.sh` | the three panes in their final state, uncut; line 2 is the development install |

**Caption truth rules, per beat.**

- Beat 1 says "Claude plans" only if Claude Code's own turn is visible doing
  it. If the take shows only the `atm plan` output, the caption is
  **Plan posted: A, then B.**
- Beat 2 says "Codex claims A in its own worktree" only with the claim line,
  a cwd or `atm who` worktree receipt, and Codex visibly doing the work. The
  test pass and the `atm review` submission must be real. If only the claim is
  visible, the caption is **A claimed by Codex.**
- Beat 3 says "A accepted" only if the verdict note with the pinned SHA is on
  screen before `atm done`. If the take shows done alone, the caption is
  **A marked done. B's task posted.** and the take does not ship as the hero,
  because the promise in section 1 is acceptance-based and was not proved.
  "Task posted" is the only word for `started:`; keep it even after the
  wording is fixed, because it stays accurate until Cursor claims.
- Beat 4 gets its caption only if the recording shows the watcher line, the
  inbox line, the Cursor launch and Cursor's claim of T-002, with no operator
  keystroke in between. If Cursor has only received the task by the time the
  highlight ends, the caption is **B posted to Cursor. Waiting for claim.**
  and beat 5 shows phase `Task posted`.
- Beat 4's line 2 always says `supervised watcher`. Cursor has no native wake
  path in this runtime (queued-offline / supervised adapter state). Do not
  write "wakes", "native", or "instantly". "Without your next command" is
  about the operator; the watcher's own `atm next` call is the mechanism and is
  on screen.
- Beat 5 needs two selections because the Work view renders review per
  selected ticket: B's `Review` row is B's own, and B's `Handoff` row is A's
  last note. Frame 1 selects T-001 for the verdict label; frame 2 selects
  T-002 for the handoff. The caption **B inherits A's handoff.** may be added
  only if beat 4 already showed `Handoff from dependencies (all notes):` in
  Cursor's claim output; a UI row proves the board holds a note, not that the
  agent received it.
- Beat 5's line 2 says `captured app frame`. The app shot is a Playwright
  screenshot taken separately from the terminal cast; it is never presented as
  continuous browser footage.
- Beat 6's install command is the README development path (`./install.sh`
  symlinks `tickets.py` to `atm` and `tickets`). CEO tightening 2: run it
  verbatim on a clean prefix after PR #129 before recording; if it prints
  anything a new user would not expect, use the README's own install block
  instead. The Homebrew formula is not published (T-865); do not show
  `brew install`. The install does not claim every agent CLI is installed.
- Beat 6's closing line is scoped to the demonstrated handoff. No caption
  claims a measured cost, turn or time reduction.
- No caption says "working" unless a claim or a running process is on screen
  at that moment.

## 4. Zoom plan and no-cut zones

Terminal layout per the CEO recipe: tmux on a private socket, 100x28 cells,
`agg --font-size 20`, caption pane on top, three agent panes below. Zoom is
`tmux resize-pane -Z` on the pane named in the table, scripted by `demo.sh`,
then unzoomed. Every zoom is bracketed by at least one second of the full
layout so the viewer keeps the map.

| Beat | Zoom target | Hold | Why |
| --- | --- | --- | --- |
| 1 | Claude pane | 6s of the 10s | the plan JSON is the only place the viewer sees cause/change/proof; the dependency line must be readable |
| 2 | Codex pane | 5s | the worktree receipt and the `pinned` / `IN REVIEW` lines are small text |
| 3 | Claude pane | 4s of the 10s | the verdict note with the SHA must be readable; unzoom before `atm done` so the eye can move to the Cursor pane |
| 4 | Cursor pane | 10s of the 12s | this is the promise; zoom in before the inbox line arrives and stay until the `Handoff from dependencies` block prints |
| 5 | app node detail, two frames | 4s + 4s | Playwright at `deviceScaleFactor 2`; click T-001, `page.screenshot` clipped to the detail panel so the `Review` row fills the frame; then click T-002 and clip to `Handoff` |
| 6 | none | 0 | closing line over the untouched final layout |

**Do not cut, compress, or zoom away from:**

- The stretch from `atm done T-001` output (beat 3) to Cursor's claim of T-002
  (beat 4). This is the handoff. It is shown in real time (section 5).
  Compressing it hides the mechanism; cutting it makes the demo a claim
  instead of a proof.
- The `watching ... for cursor-worker` line. It must be on screen before the
  task arrives, or the viewer cannot tell a watcher from a person.
- Any refusal, retry, or error in the run. Keep it in the highlight if it is
  inside a no-cut zone; otherwise it stays in the uncut cast and the
  highlight's line 2 says `waits shortened`.

## 5. Pacing

Target 55s, hard range 45 to 60s. One editing rule, applied consistently:

- **Idle-only compression everywhere except the handoff.** The cast is
  converted with `agg --idle-time-limit 2`. No content is ever removed; no
  output is trimmed; timestamps stay in the cast.
- **The beat 3 to beat 4 handoff is shown uncompressed.** Because agg's idle
  limit is global, `demo.sh` keeps that window free of idle gaps by refreshing
  the caption bar's line 2 once a second with an elapsed counter
  (`supervised watcher · real time 0:07`). Every gap in that window is then
  under the limit and agg leaves it at wall-clock length by construction. The
  recording seat confirms this by comparing the cast timestamps across the
  window to wall-clock before publishing. The counter is narration in the
  caption pane, never in an agent pane.
- **No scrolling cut, no content cut, no per-beat re-record.** If beat 2 or
  the handoff cannot fit the 60s under these rules, the recording seat asks
  the CEO for a disposition: a simpler real task A, a shorter watch poll, or a
  longer highlight. Nothing is spliced.

| Beat | Budget | Real-time expectation | Compression |
| --- | --- | --- | --- |
| 0 open | 5s | 5s | none |
| 1 plan | 10s | 10–20s (typing plus plan output) | idle only |
| 2 claim, work, review | 10s | under ~90s of real Codex work (task sized for it) | idle only; test output may scroll, never trimmed |
| 3 verdict + done | 10s | 10s | none |
| 4 watcher to Cursor | 12s | one 5s poll plus Cursor launch and claim | none; real time with the elapsed counter on line 2 |
| 5 Work view | 8s | two captured frames | none |
| 6 close | 5s | 5s | none |

The 45–60s figure is the length of the highlight. It is not a claim about how
long the task took, and no caption may suggest it is. The uncut cast, the exact
commands and the release SHA are linked under the hero.

## 6. What the demo must not imply

| Do not imply | Because | Guard |
| --- | --- | --- |
| Universal wake ("any agent wakes on its own") | only some harnesses have a native wake endpoint; the demo shows one supervised path | beat 4 line 2 names the mechanism; no caption uses "wake" |
| Native wake on Cursor | Cursor is supervised / queued-offline in this runtime | `supervised watcher` label stays on screen for all of beat 4 |
| Compliance, audit, or policy enforcement | nothing in the run shows it; it is a different product surface | no such words anywhere in the captions |
| Swarms, many agents, parallel fan-out | three named seats, one dependency edge | never say "swarm", "fleet", "scale"; show three panes and no more |
| Work began when the task was posted | `started:` is a posted message | "task posted" caption; phase `Task posted` in the Work view is fine to show |
| A was accepted because it was marked done | `atm done` records no verdict | the verdict note with the pinned SHA is on screen before `atm done`, or the caption says "marked done" and the take does not ship |
| The agent received the handoff because the app shows a Handoff row | the row proves the board holds a note | "inherits" only after `Handoff from dependencies (all notes):` printed in Cursor's claim output |
| Claude or Codex did the work because a pane is titled that | pane titles are labels | captions name an agent only when its own turn or actions are on screen |
| The run was fast | the highlight is compressed | `waits shortened` label; uncut cast linked; no measured cost or turn claim |
| The app clip is live footage | it is a Playwright screenshot | line 2 `captured app frame` |
| Atman replaces the agents or routes models | Atman assigns and carries handoffs; the agents do the work | captions name the agent that did each step |
| Homebrew install exists, or all agent CLIs come with it | formula not published; install links `atm` and `tickets` only | install command is `git clone` + `./install.sh`, labeled development install |

## 7. Handoff to the recording seat

- This document proposes the take. Runtime labels, attribution and every
  quoted string are checked on the recorded release, not assumed from the dry
  run.
- Verify each quoted string against the merged release: the `atm plan` result
  lines, the `atm next` claim and its `Handoff from dependencies (all notes):`
  block, the `atm who` `worktree` column, `pinned <branch>@<sha> in ...`,
  `T-001 -> IN REVIEW after`, `recorded <branch>@<sha>`, `unblocked:`,
  `started: T-002 -> cursor-worker`, the `watching ... every 5s; wake=...;
  cwd=...; cmd=...` line, the inbox line `unblocked T-002 after T-001 -- start
  (success trigger)`, Work view phases `Task posted` / `Working`, detail rows
  `Handoff` and `Review`, labels `Accepted by @ceo on <sha7>` and
  `Merged into main as <sha7>`.
- Record on the throwaway board only. Never the live board.
- Record continuously; keep the uncut `.cast`; convert with idle compression
  only and the handoff window kept at wall-clock length; link cast, commands
  and SHA under the hero.
- If any caption in section 3 is not true of the take, change the caption to
  the fallback given there or drop the take. Do not re-record a single beat
  and splice it.
