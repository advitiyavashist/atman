# Atman demo rehearsal (T-924)

This replaces the stale T-940 command recipe for the next recording. It is a
rehearsal kit, **not a finished demo**. No paid provider run or final cast has
been made with it. The earlier Opus working directory is untouched.

## Local checks: no providers, no remote writes

From this checkout:

```sh
python3 docs/demo/rehearsal.py setup
# Copy the printed RUN path; each setup creates a new disposable directory.
python3 docs/demo/rehearsal.py dry <RUN>
python3 docs/demo/verify.py <RUN>
python3 docs/demo/screenshot.py <RUN>
python3 -m pytest tests/test_t924_demo_rehearsal.py -q
```

Python's standard library and Git run the dry rehearsal; the browser check
also requires Python Playwright and installed Google Chrome. All board writes
go through the chosen runtime's CLI on a new temporary board. A local bare
origin and **explicit dry-only PR fixture** exercise review without GitHub.
Dry A/B code is scripted and labeled accordingly. It proves CLI transitions,
exact acceptance, dependency-note delivery and code integration; it proves
neither provider inference nor watcher launch. Nothing here edits the live
board or resets an existing directory.

`receipt.py` executes each `atm` command and records exit status, output,
timestamp, seat, cwd, and actual board state. Verification requires the ordered
plan → A claim → pinned review → exact structured acceptance → done → B claim
states, the same handoff text in B's output, and accepted-commit ancestry in
B's worktree. Echoing success captions cannot pass these checks. These are
local receipts, not tamper-proof attestation; review the original transcripts.

## Real take: wait for T-1014

Use the reviewed post-T-1014 runtime, with T-972 installation/enrollment
instructions and T-819's final CTA. Set up an **empty dedicated throwaway GitHub
repository** for the sample; inspect its ownership and emptiness before pushing.
Do not use the Atman product repository as the demo origin.

```sh
python3 docs/demo/rehearsal.py setup --mode real --runtime <REVIEWED_CHECKOUT> --origin <THROWAWAY_GITHUB_ORIGIN>
# In the new demo repo, push the seed main to the verified empty remote.
git -C <RUN>/repo push origin main
# Three panes, with an uncut cast already recording:
python3 docs/demo/seat.py <RUN> ceo --release-approved
# Wait until Claude has planned A and B and reserved their seats.
python3 docs/demo/seat.py <RUN> cursor-worker --watch --release-approved
python3 docs/demo/seat.py <RUN> codex-worker --release-approved
```

`--release-approved` is the recorder's explicit assertion that the release
gate is met, not an automatic check of the launch board. Normal harness
permissions remain in effect; no permission bypass flags are passed. Resolve
provider auth/tool-permission prerequisites before the take; an approval pause
is visible and must not be concealed. Keep hooks scoped to these worktrees.

The CEO command actually invokes Claude; Codex implements/reviews in its own
turn; the Cursor watcher invokes Cursor **before** its inbox/claim commands.
Cursor itself reads the note, merges the accepted full SHA with
`git merge --ff-only`, imports `summarize`, implements B and runs its tests.
No shell-side claim or code-writing actor is allowed in real mode.
Stream output is preserved byte-for-byte under `<RUN>/logs/`.

`atm accept T-001 --sha <FULL40> --notes '<actual checks>'` is the current
acceptance path. A prose verdict note is unverified. `atm done` posts B's task;
it does not prove Cursor claimed it, started inference, or received A's code.
The done note must include the accepted full SHA and `summarize(path)` interface.

The real process commands have been checked against local CLI help, but their
provider execution remains untested until the authorized real take. Logs plus
state checks do not alone certify model attribution: a reviewer must inspect
the provider tool calls and uncut handoff before publishing.

## 55–60 second editing target

| Seconds | Caption | Required evidence |
| --- | --- | --- |
| 0–4 | Stop retyping context between your agents. | Three real seats; no measured savings claim. |
| 4–12 | Claude plans two dependent tasks. | Claude's own plan tool call; truthful cause; real A→B edge. |
| 12–25 | Codex builds and submits the parser. | Codex tool calls, cwd, edits, tests, actual PR and review pin. |
| 25–34 | A accepted. B's task posted. | Claude's review/tests, structured full-SHA accept, then done. |
| 34–47 | Cursor starts from the accepted handoff. | Watcher launch, Cursor's own claim, note receipt, code integration and first action. Label: **Supervised watcher · real time**. |
| 47–55 | A's review. B's handoff. | Asserted screenshots from the same run, labeled **Captured app frames**. |
| 55–60 | Your agents. One handoff. | T-819's tested install CTA; final real states. |

Idle-only compression may occur before the handoff, labeled **waits shortened**.
No content cuts or spliced runs. Keep the entire done → watcher → Cursor claim
and first action interval in real time, including errors/retries. A 60-second
take is a target, not an observed duration; if real output does not fit, obtain
a revised duration or simplify the task. Preserve original cast timestamps.

The screenshot check asserts the selected ticket, review/handoff text, board
sync label, no horizontal overflow and no page errors at 1280×900 and 390×844.
It writes four labeled captures plus their asserted text to `screenshots.json`.
Do not present them as continuous browser footage.

Publication still requires: T-1014's final runtime matrix; T-972 clean install
and recovery; T-819 CTA/copy; real-seat execution and human attribution review;
the uncut cast plus commands/release SHA/PR/test receipts; and Pages link/mobile
verification. This kit does not close T-924 or T-976.
