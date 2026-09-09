# CLI gotchas and verbs you will miss

Read this before concluding a capability is missing. On one session the
coordinator nearly built staleness detection from scratch because it did not
know `pulse` existed, and filed two bug reports against behaviour that turned
out to be a deliberate safety feature working correctly.

## Verbs that are easy to miss

    pulse       heartbeat + progress-update deadlines, as JSON. Per ticket:
                heartbeat_minutes, heartbeat_due, update_due. THIS is how you
                find claims held by agents that have died.
    reopen      release a claimed ticket back to open. This is how you fix a
                stale claim -- `update` has no --status flag.
    handover    save recovery context and publish a ticket update. Use this
                before you stop, rather than a bare note.
    role        durable role: list / show / take (with an expected holder)
    mine        tickets claimed by this agent
    next        atomically claim the next available ticket
    block       mark blocked, with a reason
    context     print the shared briefing file
    knowledge   index tracked docs under docs/knowledge/: list | show (inject is brief)
    board       compact summary; `map` for the sprint/epic tree

## Where the flags actually live

Several verbs put things where you would not look for them:

- **`update <id> <text>`** takes POSITIONAL text. There is no `--note` and no
  `--status`. For a note use `note`; for status use `reopen` / `block` / `done`.
- **`assign <id> --owner <agent>`** — owner is a FLAG. `assign` takes exactly
  one positional. It also carries `--priority`, `--role`, `--epic`, `--sprint`,
  `--title` and `--notes`, so **re-ranking priority IS possible** — it is just
  not under `update`.
- **`done <id> --notes "..."`** — the notes are REQUIRED (pass `--no-notes` only
  if there is genuinely nothing to hand off).

## The strictness is deliberate. Do not route around it.

Three guards that will stop you, all of them load-bearing:

1. **`done` requires `--notes`.** The prompt asks for "paths, names, decisions
   the next agent must match". Write them for the next agent, not for the log.
   This caught a coordinator trying to close two decision tickets with no
   handoff at all.
2. **`done` refuses to run from `main`** without `--force`, on the grounds that
   work belongs on the agent's own branch.
3. **`done` refuses if the recorded repository does not match the current one**,
   and `--force` cannot override that. It correctly stopped three code tickets
   from being closed from the board's own directory.

## `NO CHANGE WAS MADE` is a feature. Do not pipe it away.

`_LoudArgumentParser` deliberately appends `NO CHANGE WAS MADE` as the trailing
line of any argument error. The reason is in its docstring: a caller reading
only stdout sees an empty string and concludes nothing went wrong, and when the
rejected argument is free text like a `--notes` value, argparse's
"unrecognized arguments" message **echoes that text back**, so the output reads
exactly like the caller's own note succeeding. Making the *tail* of the output
the warning is the fix.

It works. It caught a coordinator twice in ten minutes. Both times the
coordinator had run the command through `| tail -1`, saw only the warning, and
concluded the tool had failed silently with no reason given. Run bare:

    $ tickets update T-003 --status open --note x
    usage: tickets [-h] {create,assign,...} ...
    tickets: error: unrecognized arguments: --status --note x
    tickets: NO CHANGE WAS MADE

The reason is right there on the line above. **Never pipe this CLI through
`tail`, `head` or `grep`** — you will throw away the half you need and, on a
free-text argument, be actively misled.

## Running from a linked git worktree: set `TICKETS_DIR`

`_repo_root()` deliberately resolves to the **main** git worktree, so every
linked worktree of a repo shares one board. That is the right default.

But it breaks when the board is not inside the repo you are working in. An agent
editing `/src/...` from a linked worktree while the board lives at
`/boards/project/.tickets` gets a bare "no board" from `tickets review`, because
the CLI resolved to the main worktree and looked there.

    # from a worktree whose board lives elsewhere
    TICKETS_DIR=/path/to/board/.tickets tickets review T-0NN

Git-state detection (branch, sha, dirty) still comes from the current working
directory and is correct — it is only board *discovery* that follows the repo
root. So `done`'s branch@sha evidence and its refusal to close from `main`
behave normally; you just have to tell it which board.

Worth knowing alongside the guard that `done` refuses when the recorded
repository does not match the current one. Those two together mean a
cross-repo board needs `TICKETS_DIR` on the way in and the right repo on the
way out.

## A dots-only pytest red is a killed runner, not a failing test (T-538)

Before you FIX-FIRST on a red merge, look at the shape of the output, not just
the exit code:

    tests/test_t427_watch_reexec.py ..
    EXITCODE=143

No `F`, no `E`, no `=== N failed, M passed ===` summary line. pytest **always**
prints a summary when a test fails, so output that stops mid-progress-line is a
process that was killed, not a suite that failed. Exit 143 is 128+15, i.e.
SIGTERM; 137 is 128+9, SIGKILL.

The kill has been called on the code under review more than once. It was
neither. `tests/conftest.py` reaps every `tmp_path` after every test via
`watch_reaper.reap_watchers_under()`, which `rglob`s `*.watch.pid` and SIGTERMs
whatever integer it reads. That pid is written by the code under test, and
`tickets.py`'s `watch_idle_reexec` writes `str(os.getpid())` — so a test that
calls the helper in-process plants the **pytest process's own pid** where the
reaper looks, and the teardown shoots the runner. `kill_pid_tree` now refuses
any pid that is this process or one of its ancestors; see
`tests/test_t538_reaper_self_kill.py`.

The discriminator, which costs about 12 seconds:

1. Dots and no summary line → treat it as a kill, not a failure.
2. Re-run the single suspected test **isolated**. A real defect reproduces
   isolated; a kill does not, and neither does a load-sensitive flake.
3. Only believe a failure that comes with an `F`/`E` *and* a summary line.

Corollary: "it also dies when run alone, so it is not concurrency" does not
mean "it is therefore the code". A self-inflicted kill is perfectly
reproducible on an idle box, and so is unaffected by quieting it.
