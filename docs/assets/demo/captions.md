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

One unedited run, recorded with asciinema, rendered with `agg --idle-time-limit 1.2`.
The only editing is idle-time compression; no beat was cut, re-ordered or re-shot,
and every command on screen is a command that executed.

Real work, on a disposable board and a throwaway repo:

- a Codex coordinator set the objective and planned two tickets with a real dependency
- a Codex worker implemented A in its own worktree, ran its tests, opened a real PR
- the coordinator independently reviewed the PR diff, ran the tests itself, and
  recorded `atm accept` bound to the full 40-character commit (reviewer != author)
- B unblocked automatically and carried A's accepted commit in its handoff
- a Cursor agent implemented B on top of that exact commit; 6 tests pass

## Caption correction after recording

Two caption lines were reworded after the take, and nothing else in the recording was touched. The original lines said "B is blocked until A is accepted" and implied acceptance releases B. In this run acceptance did happen before A was marked done, so what the take shows is true -- but the runtime does not yet ENFORCE that order: a ticket marked done without an accept still releases its successors (reproduced on a throwaway board; fix tracked as T-1031). The captions now describe what happened rather than asserting a rule the product does not enforce. No command output was altered.

## Not shown, deliberately

- the local app UI (terminal-only take; app capture is a separate asset)
- installation (covered by the onboarding docs)
- any capability we cannot demonstrate: no speed claim, no cost claim, no
  "autonomous" framing, and nothing scripted or re-enacted

## Reproduce

`record-take.sh` is the exact script used, and `demo.cast` is the raw recording.
The run directory is created by `docs/demo/rehearsal.py setup --mode real --origin <repo>`.
