# Atman landing demo: storyline and on-screen script

Status: T-940 deliverable for the T-924 hero recording. Doc only. Sol reviews
for honesty; CEO approves before anything is recorded. Companion to Sol's
`atman-demo-storyboard.md` (honest-cuts and wake-label rules there still apply);
this doc adds the customer lens, the beat-by-beat captions, the zoom plan and
the pacing budget.

Every string quoted below in `code` is what the runtime at 786be1f actually
prints or renders. The recording seat re-checks each one against the merged
release before recording. If the run prints something else, the caption
changes to match the run, never the other way round.

## 1. The viewer, the pain, the promise

**Viewer.** An engineer who already runs Claude Code, Codex or Cursor, often
two of them in the same week. They lose time in two places: re-explaining the
same context to the next agent, and hand-carrying "A is done, now do B" between
terminals.

**Pain to open on (first 5 seconds, no jargon).** One line, on screen before
any command runs:

> You finish a task in one agent, then retype it all for the next one.

**The one-sentence promise the demo proves.**

> When one agent finishes A, the next agent gets B with A's handoff, and nobody
> types "next".

That is the whole claim. The recording either shows it or the demo does not
ship. Everything else Atman does (liveness, limits, cost, review queue) stays
out of the hero.

## 2. The real run behind the beats

Same run as the CEO dry run of 2026-09-14 12:15 on a throwaway repo, with real
agent processes:

| Seat | Harness | Pane title | Does |
| --- | --- | --- | --- |
| `ceo` (master) | Claude Code | `Claude Code (CEO)` | joins, sets objective, `atm plan` A then B, accepts A |
| `codex-worker` | Codex | `Codex (worker)` | `atm next` claims A in its own worktree, works, `atm review` |
| `cursor-worker` | Cursor | `Cursor (worker)` | sits behind `atm watch`; receives B's task, claims, starts |

Task shape (from Sol's storyboard): A adds a small CSV summary function with a
test; B adds a command that calls A's function. B's `proof` names A's interface,
so the Handoff row in the Work view has something real to show.

**Preconditions before recording, all from the CEO dry run:**

1. PR #124 (T-883, "do not wake live fleet names on throwaway boards") must be
   merged. Still OPEN at the time of writing. Without it `atm done` wakes seats
   named `cursor` and `atman-ceo` that do not exist on the throwaway board and
   the recording shows ghost seats.
2. `atm done` prints `started: T-002 -> cursor-worker`. That line means a task
   message was posted to the seat, not that work began. Until the wording is
   fixed, the caption over that line says **task posted**, never "started".
3. `atm reserve` needs `atm master take` first even after `join --roles master`.
   Do it before recording starts; it is setup, not story.
4. `atm done` refuses to run from the main checkout. The CEO pane runs from its
   own worktree. This is a feature and can stay visible if it happens; do not
   cut a refusal and retry it off camera.
5. Global Cursor and Claude hooks that pin an identity (T-856, T-939) are
   removed on the recording machine. Seats get worktree-scoped hooks only.

## 3. Beats, captions, evidence

Caption bar: a 2-line pane at the top of the tmux layout, written by
`demo.sh` at each beat (CEO 12:40 addition). Line 1 is the caption, at most 8
words, plain. Line 2 is a status label that stays true for the whole beat.
Narration only; the bar never mimics tool output. The label
`Highlights · waits shortened` from Sol's storyboard lives on line 2 whenever
idle time was compressed in that beat.

| # | Time | Pane in focus | Caption (line 1, ≤ 8 words) | Line 2 | What must be visible on screen for the caption to be true |
| --- | --- | --- | --- | --- | --- |
| 0 | 0–5s | full layout, no zoom | **Stop retyping context between your agents.** | `Claude Code · Codex · Cursor` | three titled panes, all idle, no board state yet |
| 1 | 5–15s | Claude pane | **Claude plans A, then B.** | `atm plan · real cause / change / proof` | the `atm plan` heredoc with `cause`, `change`, `proof` for both items and `deps: ["A"]` on B; the result showing T-001 ready and T-002 waiting on T-001 |
| 2 | 15–25s | Codex pane | **Codex claims A in its own worktree.** | `atm next · worktree .worktrees/codex-worker` | the `atm next` output naming T-001 and `codex-worker`; the worktree path in the prompt; the test run passing; `atm review T-001 --notes ...` printing `T-001 -> IN REVIEW after ...` |
| 3 | 25–33s | Claude pane | **A accepted. B's task posted to Cursor.** | `atm done T-001 · success trigger` | `atm done T-001` output: `T-001 done in ...`, `unblocked: T-002`, `started: T-002 -> cursor-worker` |
| 4 | 33–47s | Cursor pane, zoomed | **Watcher hands B to Cursor. Nobody typed next.** | `supervised watcher · not a native wake` | the earlier `watching <board> for cursor-worker every Ns; wake=...` line; the inbox line `unblocked T-002 after T-001 -- start (success trigger)`; the watcher launching Cursor and Cursor's own claim of T-002 |
| 5 | 47–55s | app Work view, node detail zoomed | **B inherits A's handoff and verdict.** | `Work view · T-002` | T-002 node in phase `Working` (or `Task posted` if that is where the run is); detail rows `Handoff` showing A's review notes and `Review` on T-001 showing `Accepted by @ceo on <sha>` |
| 6 | 55–60s | caption bar over the full layout | **Your agents. One plan. No retyping.** | `git clone https://github.com/advitiyavashist/atman.git && cd atman && ./install.sh` | the three panes in their final state, uncut |

**Caption truth rules, per beat.**

- Beat 3 says "task posted" because that is all `atm done` does. If the run is
  recorded after the `started:` wording is fixed, keep "task posted" anyway; it
  is still the accurate word until Cursor claims.
- Beat 4 gets the caption above only if the recording shows the Cursor claim of
  T-002 with the watcher line and the inbox line before it. If Cursor has only
  received the task by the time the highlight ends, the caption is
  **B posted to Cursor. Waiting for claim.** and beat 5 shows phase
  `Task posted`.
- Beat 4's line 2 always says `supervised watcher`. Cursor has no native wake
  path in this runtime (queued-offline / supervised adapter state). Do not
  write "wakes", "native", or "instantly".
- Beat 5 shows `Accepted by @ceo on <sha>` only if the CEO actually ran
  `atm done T-001` with the artifact recorded. If the Review row reads
  anything else, the caption becomes **B inherits A's handoff.** and the
  verdict half is dropped.
- Beat 6's install command is the README development path. The Homebrew
  formula is not published (T-865); do not show `brew install`.
- No caption says "working" unless a claim or a running process is on screen at
  that moment.

## 4. Zoom plan and no-cut zones

Terminal layout per the CEO recipe: tmux on a private socket, 100x28 cells,
`agg --font-size 20`, caption pane on top, three agent panes below. Zoom is
`tmux resize-pane -Z` on the pane named in the table, scripted by `demo.sh`,
then unzoomed. Every zoom is bracketed by at least one second of the full
layout so the viewer keeps the map.

| Beat | Zoom target | Hold | Why |
| --- | --- | --- | --- |
| 1 | Claude pane | 6s of the 10s | the plan JSON is the only place the viewer sees cause/change/proof; the dependency line must be readable |
| 2 | Codex pane | 5s | the worktree path in the prompt and the `IN REVIEW` line are small text |
| 3 | none | 0 | the `atm done` output is three short lines; keep the full layout so the eye can move to the Cursor pane on the next beat |
| 4 | Cursor pane | 10s of the 14s | this is the promise; zoom in before the inbox line arrives and stay until the claim prints |
| 5 | app node detail | 8s | Playwright at `deviceScaleFactor 2`, click node T-002, `page.screenshot` clipped to the detail panel so the `Handoff` and `Review` rows fill the frame |
| 6 | none | 0 | closing line over the untouched final layout |

**Do not cut, compress, or zoom away from:**

- The stretch from `atm done T-001` output (beat 3) to the Cursor inbox line
  (beat 4). This is the handoff. Compressing it hides the mechanism; cutting
  it makes the demo a claim instead of a proof.
- The `watching ... for cursor-worker` line. It must be on screen before the
  task arrives, or the viewer cannot tell a watcher from a person.
- Any refusal, retry, or error in the run. Keep it in the highlight if it is
  inside a no-cut zone; otherwise it stays in the uncut cast and the
  highlight's line 2 says `waits shortened`.

## 5. Pacing

Target 55s, hard range 45 to 60s. The only compression is idle time:
`agg --idle-time-limit 2` on the uncut cast, no content edits. The uncut cast
and the exact commands plus SHA are linked under the hero.

| Beat | Budget | Real-time expectation | Compression |
| --- | --- | --- | --- |
| 0 open | 5s | 5s | none |
| 1 plan | 10s | 10–20s (typing plus plan output) | idle only |
| 2 claim, work, review | 10s | 1–4 min of real Codex work | idle only; test output may scroll, do not trim it below its pass line |
| 3 accept | 8s | 8s | none |
| 4 watcher to Cursor | 14s | up to one watch interval plus Cursor launch | idle only; keep the interval label honest on line 2 if it was long (`waits shortened`) |
| 5 Work view | 8s | 8s | none |
| 6 close | 5s | 5s | none |

If beat 2 cannot be brought under 12s with idle compression alone, the
highlight shows the claim, the passing test line and the `IN REVIEW` line and
leaves the middle to the uncut cast. That is a cut of scrolling, not of
events, and line 2 says so.

The 45–60s figure is the length of the highlight. It is not a claim about how
long the task took, and no caption may suggest it is.

## 6. What the demo must not imply

| Do not imply | Because | Guard |
| --- | --- | --- |
| Universal wake ("any agent wakes on its own") | only some harnesses have a native wake endpoint; the demo shows one supervised path | beat 4 line 2 names the mechanism; no caption uses "wake" |
| Native wake on Cursor | Cursor is supervised / queued-offline in this runtime | `supervised watcher` label stays on screen for all of beat 4 |
| Compliance, audit, or policy enforcement | nothing in the run shows it; it is a different product surface | no such words anywhere in the captions |
| Swarms, many agents, parallel fan-out | three named seats, one dependency edge | never say "swarm", "fleet", "scale"; show three panes and no more |
| Work began when the task was posted | `started:` is a posted message | "task posted" caption; phase `Task posted` in the Work view is fine to show |
| The run was fast | the highlight is compressed | `waits shortened` label; uncut cast linked |
| Atman replaces the agents or routes models | Atman assigns and carries handoffs; the agents do the work | captions name the agent that did each step |
| Homebrew install exists | formula not published | install command is `git clone` + `./install.sh` |

## 7. Handoff to the recording seat

- Verify each quoted string against the merged release: the `atm plan` result
  lines, `atm next` claim line, `T-001 -> IN REVIEW after`, `unblocked:`,
  `started: T-002 -> cursor-worker`, the `watching ... wake=` line, the inbox
  line `unblocked T-002 after T-001 -- start (success trigger)`, Work view
  phases `Task posted` / `Working`, detail rows `Handoff` and `Review`,
  `Accepted by @ceo on <sha>`.
- Record on the throwaway board only. Never the live board.
- Record continuously; keep the uncut `.cast`; convert with idle compression
  only; link cast, commands and SHA under the hero.
- If any caption in section 3 is not true of the take, change the caption to
  the fallback given there or drop the take. Do not re-record a single beat
  and splice it.
