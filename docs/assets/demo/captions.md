# Demo take (T-924): captions, evidence and how it was made

## Captions

On-screen narration, in order, as plain text so a landing page can render its own
overlays, restyle or translate without re-recording.

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

## What happened in the take

Real agents on a disposable board and a private throwaway GitHub repository:

- a **Codex coordinator** set the objective and planned two tickets with a real dependency
- a **Codex worker** implemented A in its own worktree, ran its tests, pushed and opened a real PR
- the coordinator **independently reviewed the PR diff and re-ran the tests**, then recorded
  `atm accept` bound to the full 40-character commit; the reviewer seat differs from the worker
- A was marked done and B unblocked, with A's accepted commit in B's handoff
- a **Cursor worker** implemented B on top of that commit; its tests pass, it pushed, and the
  last command on screen checks with git that A's accepted commit is an ancestor of B

## What the product does and does not enforce today

In this take acceptance happened **before** A was marked done. The runtime does not yet
*require* that order: a ticket marked done without an accept still releases its successors
(reproduced on a throwaway board; fix tracked as T-1031). The captions therefore describe
what happened, not a rule. Do not caption this demo as "B cannot start until A is accepted"
until T-1031 merges.

## How it was edited

- **Idle time is compressed** when rendering the GIF (`agg --idle-time-limit 1.2`). No beat
  was cut, re-ordered or re-shot, and no command output was changed.
- **Terminal output of each agent turn is its tail.** The full provider output of every turn
  is in `evidence/` — the screen shows the last lines of the same log file.
- **The published cast header was sanitised**: its `command` field and `env` block named an
  operator-specific scratch path; they now read `record-take.sh <RUN>`. Evidence files have
  the run directory replaced with `<RUN>` and the home directory with `$HOME` / `<operator>`.
  No event text, timing or output in the cast body was altered by that step.
- An earlier take (PR #211 at `fda7429` / `bac8b4a`) is **superseded** by this one: its run
  directory was lost to a restart, so its execution could not be independently verified, and
  two of its captions were corrected after recording. This take was recorded with the correct
  captions from the start.

## Evidence

| file | what it shows |
|---|---|
| `evidence/1-plan.log` | full coordinator output for the planning turn |
| `evidence/2-worker.log` | full worker output: implementation, tests, push, PR, `atm review` |
| `evidence/3-accept.log` | full coordinator output: PR inspection, independent test run, `atm accept` |
| `evidence/4-cursor.log` | the Cursor worker's output for B |
| `evidence/5-board-state.txt` | `atm show` for both tickets and the final board, including the accept line |
| `evidence/6-git-history.txt` | both worktrees' commits: B's commit sits directly on A's accepted commit |

## Reproduce

```sh
python3 docs/demo/rehearsal.py setup --mode real --origin <empty throwaway repo>   # prints RUN=...
python3 docs/assets/demo/split_prompts.py <RUN>
asciinema rec -c "zsh docs/assets/demo/record-take.sh <RUN>" demo.cast
agg --idle-time-limit 1.2 --font-size 16 --theme asciinema --last-frame-duration 4 demo.cast hero.gif
```

Keep `<RUN>` outside any directory your OS clears on reboot, or the evidence goes with it.
