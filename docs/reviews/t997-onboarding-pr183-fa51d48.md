# T-997 — first-user onboarding review

**Verdict: FIX**

Reviewed Atman PR #183 at exact candidate
`fa51d4894b8a8c46a77efc8973d33f79ecc91f4f` for the source-prefix macOS
developer preview. This review did not run a provider model, change the
candidate, run the full suite, or merge it.

## What worked

I followed the candidate's documented path on a new throwaway Git repository
with an isolated `HOME`, prefix, and board:

- `./install.sh --prefix <scratch>/bin` installed `atm` and `tickets` as
  symlinks to the same exact candidate `tickets.py`; `atm where` resolved the
  project board.
- `atm quickstart`, `atm quickstart --remove`, and committing the generated
  `AGENTS.md`, `.cursor/`, and `.gitignore` worked. The first real tickets were
  T-001 and T-002 as documented.
- A measurable objective and a real T-001 -> T-002 dependency worked. T-002
  stayed unavailable until T-001 was accepted, merged, and marked done.
- Two manually driven no-provider seats claimed from their own worktrees,
  submitted exact artifacts, and preserved T-001's notes in T-002's inherited
  handoff. `accept`, merge, and `done` remained distinct events.
- A foreground watcher with `/usr/bin/true` woke once on a directed `--task`
  message and stopped at `--max-runs 1`; no provider call was made.
- Reopening an interrupted T-003 released the old owner. A replacement seat
  claimed it and saw both the interrupted worker's progress note and the
  coordinator's reopen note.
- `atm ui --host 127.0.0.1 --port 18885` served both `/` and `/board.json`.
  The JSON truthfully showed two done tickets, unknown turns/cost, and the
  task-message wake receipt.
- The candidate's focused contract set passed 68/68. The main batch passed
  59 tests; the sole failed watcher test passed when rerun outside the macOS
  sandbox, whose process inspection had returned an empty `ps` result; the
  remaining eight T-971 tests passed separately.

## Launch-blocking gaps

1. **The promised provider-neutral first run becomes Claude-only.**
   `first-run.md:3` addresses a user with Claude, Codex, *or* Cursor and
   `first-run.md:177-179` tells them to choose an installed, logged-in
   harness. Both actual worker commands at `first-run.md:222` and
   `first-run.md:323` then hardcode `--harness claude --model sonnet`. A
   Cursor-only or Codex-only customer cannot follow the advertised route.
   Give one `HARNESS` choice with tested per-provider commands/model handling,
   or narrow the preview honestly to Claude.

2. **The walkthrough has no success endpoint.** After both tickets pass their
   exit criteria, `first-run.md:345-355` deliberately displays `OBJECTIVE --
   ACTIVE`; it never runs `atm objective --done <evidence>`. The reproduced
   board behaved exactly that way. Add the explicit achieved command and show
   the resulting state. A first run must end in a completed objective rather
   than an empty board under an active objective.

3. **The walkthrough never launches the product app.** There is no `atm ui`
   command anywhere in `first-run.md`, although the local app is the preview's
   product surface and it worked in this review. Add one final `atm ui` beat
   and tell the reader what evidence should be visible: objective state,
   dependency result, exact review verdict, handoff, and unknown cost/turns.

4. **Recovery is absent from the first-user path.** The guide never uses
   `atm reopen` or shows another seat inheriting an interrupted worker's
   notes. The underlying path worked in this review, but a user cannot learn
   it from the promised end-to-end guide. Add one bounded interruption:
   worker update -> coordinator reopen with reason -> replacement claim ->
   prior notes visible. This can replace prose rather than grow a new guide.

5. **The onboarding set claims remote support that the launch scope excludes.**
   `docs/onboarding/README.md:16` routes users to remote-session wiring;
   `docs/onboarding/reference.md:107-109` instructs them to use
   `--harness remote` and `atm hooks remote`; and
   `docs/onboarding/ceo-connect.md:80` presents `tickets hooks remote` in the
   operational command path. The preview says remote control is unfinished.
   Remove these from public onboarding or label them experimental and outside
   the supported preview path, with no implication that reconnect works.

## Product-output contradictions exposed by the walkthrough

These are implementation follow-ups, not a reason to rewrite the guide around
incorrect behavior:

- Fresh `atm join` and every `atm next` told a worker to finish with
  `tickets done`, bypassing the guide's correct `review -> accept -> merge ->
  done` gate. Generated worker instructions must end at `atm review`; only the
  coordinator closes merged work.
- Fresh `atm harness available` exposed repository-specific operator policy
  such as `no new Claude fable`, `do not spawn a Gemini product job`, and a
  Codex quota instruction on a brand-new external project. The public harness
  catalog should report capability/auth/usage facts and generic next actions;
  board-specific policy belongs in that board's briefs.
- `quickstart` output still teaches the `tickets` alias as the primary command.
  The guide correctly uses `atm`; generated day-one output should match it.

## Bounded repair and re-review

Keep the source-prefix-only install and the existing A -> B example. Repair
the five onboarding gaps above, then replay only this walkthrough and the same
68 focused tests. Track the three generated-output contradictions as explicit
launch blockers in their owning implementation tickets; do not mask them with
extra documentation.
