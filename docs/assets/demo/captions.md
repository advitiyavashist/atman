# Demo captions (T-924)

On-screen caption text for the hero take, in order. Kept as plain text so a
landing page can render its own overlays, restyle or translate without
re-recording. Timings are approximate beat boundaries in the compressed GIF.

| # | Beat | Caption |
|---|---|---|
| 0 | title | Atman — your agents, one board, one handoff |
| 1 | plan | Give a coordinator agent an objective. It plans the work on the board. |
| 2 | board | Two tickets, one real dependency: B waits for A. |
| 3 | work | A worker agent claims A in its own worktree, tests it, opens a PR. |
| 4 | accept | The coordinator reviews the work itself — then accepts it against the exact commit. |
| 5 | unblock | A is accepted and done. B unblocks, carrying A's accepted commit in its handoff. |
| 6 | handoff | A Cursor agent — different vendor — picks up B. Nobody retypes what A did. |
| 7 | proof | B built on A's accepted commit. Real code, real tests. |
| 8 | end | github.com/advitiyavashist/atman |

## What the take actually is

The terminal output comes from one completed Codex/Cursor run recorded with
asciinema. The GIF uses `agg --idle-time-limit 1.2`, so idle time is compressed.
Two narration payloads were corrected after recording and the cast header was
sanitized; the command-output events were not changed. No beat was cut,
re-ordered or re-shot.

Real work, on a disposable board and a throwaway repo:

- a Codex coordinator set the objective and planned two tickets with a real dependency
- a Codex worker implemented A in its own worktree, ran its tests, opened a real PR
- the coordinator independently reviewed the PR diff, ran the tests itself, and
  recorded `atm accept` bound to the full 40-character commit (reviewer != author)
- B unblocked after A was done and carried A's accepted commit in its handoff
- a Cursor agent's provider transcript records an ff-only merge of that exact
  commit before it implemented B; 6 tests pass

## Publication changes after recording

Two caption lines were reworded after the take. The original lines said "B is
blocked until A is accepted" and implied acceptance releases B. In this run
acceptance did happen before A was marked done, but the runtime did not yet
enforce that order: a ticket marked done without an accept still released its
successors (tracked as T-1031). The captions now describe what happened. The
cast header command was also replaced with a parameterized, operator-neutral
path. No command output or body timing was altered by those corrections.

The recovered provider receipts are sanitized excerpts: system/developer prompt
boilerplate is omitted and local paths are replaced. The Cursor commit was not
pushed and the temporary worktree was deleted, so its Git object ancestry is no
longer independently checkable. The transcript still preserves Cursor's own
ff-only merge command and subsequent implementation/test record. See
`evidence/README.md` for the evidence boundary and exact digests.

The cast shows the tail of each provider turn, not its complete output. The
restored receipts include recoveries omitted by those tails: the planner retried
ticket reservations under the correct coordinator identity, and the Codex worker
recovered from a stale remote branch and a merge conflict before it pushed the
unique reviewed branch. Those intermediate failures are part of the run.

## Not shown, deliberately

- the local app UI (terminal-only take; app capture is a separate asset)
- installation (covered by the onboarding docs)
- any capability we cannot demonstrate: no speed claim, no cost claim, no
  "autonomous" framing, and nothing scripted or re-enacted

## Reproduce

`demo.cast` contains the published event stream with the header and caption
corrections described above. `record-take.sh` is a parameterized replay of its
ten-command sequence. The original prompts are preserved under
`evidence/prompts/`; install them into a fresh isolated run with
`prepare-replay.py` before replaying. A replay produces new model output and is
not the published take.
