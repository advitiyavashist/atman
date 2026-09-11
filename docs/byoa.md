# Bring your own agent

Operator guide for running an arbitrary harness on this board: a local
Qwen through Ollama, a Python script, an HTTP agent, a shell one-liner.

**Standing role context** (know → inject → update on every wake) is separate
from harness wiring: [onboarding/role-context.md](onboarding/role-context.md).
Every harness — built-in or `custom:<cmd>` — receives the same prompt file.

Masters: smoke with a custom dry harness before a paid CLI — copy-paste in
[onboarding/master-howto.md](onboarding/master-howto.md) (Step 5).

Every command and every output block below was run against a throwaway board
before being written down. The examples use a stub harness rather than a real
model, because what is being demonstrated is the *runtime's* half of the
contract; nothing here is illustrative of a model's behaviour.

For interactive or remote harness hooks, see [hook identities](hook-identities.md).
Those installers bake a separate board identity into each harness instead of
depending on the launching shell.

## The contract, in one paragraph

The runtime hands your harness a **prompt file**, a **working directory** and
an **identity**, then reads the board afterwards to see what happened. That is
the whole interface. Your harness does not import anything, does not implement
a protocol, and does not call back into the runtime except through the same
`tickets` CLI every other agent uses.

```
{prompt_file}   path to a file containing this agent's prompt for THIS wake-up
{cwd}           the agent's own git worktree (the process already starts here)
{agent}         the agent's name on the board
```

Two environment variables come with it: `TICKET_AGENT` (the same name) and
`TICKETS_DIR` (the board), so a harness that shells out to `tickets` needs no
configuration of its own.

## Register a harness

Match the lane you want to work. After `tickets quickstart`, the sample tickets
are `role=backend`, so register `--roles backend` (same as
[first-session.md](first-session.md) and the worker loop in README.md). A
`docs` seat is fine once the board has `role=docs` work — see the footgun in
[onboarding/master-howto.md](onboarding/master-howto.md).

```sh
tickets join qwen --roles backend \
  --harness custom --cmd '/tmp/byoa2/echo-agent.sh {prompt_file} {cwd} {agent}'
```

```
joined as qwen  roles=['backend']  can=-  cost=medium  harness=custom
cmd: /tmp/byoa2/echo-agent.sh {prompt_file} {cwd} {agent}
board: /tmp/byoa2/repo/.tickets
```

`--harness custom:<cmd>` is the same thing in one flag, for a spawn line in a
shell script. `--tool` is the original spelling of `--harness` and still works.
The built-in names -- `claude`, `codex`, `cursor`, `cursor+claude`, `agy`, `antigravity`, `devin`, `cognition` (see [connect-agy.md](connect-agy.md) and [connect-devin.md](connect-devin.md)) -- need no
`--cmd`; they expand to that CLI's headless invocation.

The command is a **shell template**, not an argv list: it is run through
`/bin/sh`, so pipes, redirection and `&&` all work. Placeholders are
substituted with `str.replace` and each substituted value is shell-quoted.
Braces that are not placeholders pass through untouched, which is what lets a
`curl` with a JSON body be a harness.

A `--harness custom` with no `--cmd` is refused at the point of the mistake
rather than registering an agent no watcher can start.

### Three shapes that work

A local model through Ollama -- the prompt on stdin:

```sh
tickets join qwen --roles backend \
  --harness custom --cmd 'ollama run qwen3:8b < {prompt_file}'
```

A Python script -- the prompt as a path, so it can read it in pieces:

```sh
tickets join scripted --roles backend \
  --harness custom --cmd 'python3 ~/agents/my_agent.py --prompt {prompt_file} --cwd {cwd}'
```

An HTTP agent -- braces in the payload are left alone:

```sh
tickets join remote --roles backend --harness custom --cmd \
  'curl -sS -X POST https://my-agent.internal/run --data-urlencode prompt@{prompt_file} -d "{\"agent\":\"x\"}"'
```

For a model session that stays connected elsewhere, use the explicit remote
adapter contract instead of naming an executable that is not installed here:

```sh
tickets join grok-worker --roles cos --harness remote --wake-mode continuous
tickets hooks remote --agent grok-worker --prompt-kind cos
```

The generated schema-2 manifest gives the remote service an identity-pinned
wrapper and command templates to register one fenced lease, heartbeat,
long-poll and atomically claim `next`, record `start`/`end`, and release. Directed
DMs and `@mentions` wake a `continuous` seat immediately. A disconnected adapter
leaves the wake queued and visible in `tickets ui`; reconnecting can claim it.
Atman never substitutes Cursor, Claude, or Codex for a remote identity. Use
`--wake-mode task-only` when the seat should run only for explicit `--task`
messages, or `scheduled` when heartbeat and explicit task gates should drive a
persistent adapter without ordinary DMs spending a turn. `scheduled` does not
create a schedule; configure a heartbeat or external cadence separately.

This adapter protocol is part of the root/live `tickets.py` installed by
`install.sh`. The current `pyproject.toml` entry point is the smaller core-board
CLI and does not expose runtime wake commands.

Use the file, not `$(cat {prompt_file})`. A worker prompt is the standing
brief plus ticket context -- 1.5 KB on an empty board, far more on a real one
-- and inlining it into a command line is how you meet `ARG_MAX`.

## Check it before you trust it

```sh
tickets harness check qwen
```

```
checking qwen: harness=custom cmd=/tmp/byoa2/echo-agent.sh {prompt_file} {cwd} {agent}
  cmd:      /tmp/byoa2/echo-agent.sh /var/folders/.../tickets-prompt-qwen-ldpjocfa.txt /private/tmp/byoa2/repo qwen
  exit:     0
  latency:  0.8s
  replied:  no 'OK' in the first 400 chars
  output:   prompt is        8 bytes; cwd /private/tmp/byoa2/repo; agent qwen
qwen: harness OK
```

The probe runs the **real command shape** -- same binary, same flags, same
template -- with only the prompt swapped for `reply OK`, under a 60 s cap
(`--timeout`). The failure it exists to catch lives in the command, not in the
model's answer: not installed, not logged in, wrong template, no network.

Before running anything, `harness check` validates the template: only
`{prompt_file}`, `{cwd}` and `{agent}` are allowed as placeholders, and
`{prompt_file}` is required. An unknown token like `{foo}` or a template with
no `{prompt_file}` fails immediately with the offending name in the message.
Braces in JSON bodies and other non-placeholder text are left alone.

The verdict is **exit status only**. `replied` reports separately whether the
output contained `OK`, and deliberately does not gate the verdict: a harness
that prefaces its answer is working, and failing it on a wording difference
would take a live agent out of the fleet.

The result is written to the agent record, so `spawn --list` and `harness list`
show who is actually reachable:

```
agent          watcher   harness   model    check        seen     worktree
qwen           pid 64905 custom    -        ok 0.8s      0m ago   /private/tmp/byoa2/repo/.worktrees/qwen
```

Exit code is 0 on pass, 1 on fail, so `harness check` is usable in a script.

For a built-in CLI, check credentials without starting a model first:

```sh
tickets harness auth cursor-seat
# If it says Login required:
tickets harness auth cursor-seat --login
```

The login form runs the harness's interactive login and immediately verifies
the resulting identity. Cursor recovery prints the exact `agent login` action.
It reports `Login required`, `Usage quota reached`, and a missing CLI as
different states, and the dashboard shows the same state. If a machine stopped
mid-watcher, add `--recover-stale`; it removes only a dead PID lock and refuses
when the recorded watcher is alive. `tickets spawn` performs this cheap check
automatically for Cursor and leaves the seat's roles, harness, and worktree
registration unchanged when login is missing. Auth V2 (execution context,
opaque credential profiles, repo identity) is specified in
[t685-auth-v2-contract.md](t685-auth-v2-contract.md); T-686 implements it.

## Run it

```sh
tickets spawn qwen --every 3600
```

```
worktree /tmp/byoa2/repo/.worktrees/qwen (branch qwen)
watcher for qwen started (pid 64905); harness=custom; model=default; log .../qwen.watch.log
cmd: /tmp/byoa2/echo-agent.sh {prompt_file} {cwd} {agent}
```

`spawn` with no `--harness` uses whatever `tickets join` registered. (Before
T-314 it defaulted to `claude`, so a registered BYOA agent silently reverted to
the Claude CLI on every spawn.) Passing `--harness` overrides *and*
re-registers; if the harness changes and no new `--cmd` is given, the old
harness's command template is dropped rather than carried over, so a switch
cannot silently launch the previous runner.

From the watch log, one real wake-up:

```
2026-09-07T13:51:04Z run 1 trigger={"ready_in_my_lane": ["T-001 Write docs"]}
prompt is     1542 bytes; cwd /tmp/byoa2/repo/.worktrees/qwen; agent qwen
2026-09-07T13:51:05Z run 1 exit 0
```

The prompt file is written fresh for every run and removed after it. It has to
be: the board changed since the last run, or the watcher would not have woken
up. Do not cache its path.

## What the runtime guarantees

- **One ticket at a time.** `tickets next` claims atomically with an O_EXCL
  lock; two harnesses racing for the same ticket cannot both win.
- **Its own worktree and branch.** `spawn` creates `.worktrees/<agent>` on
  branch `<agent>` and starts the harness there. Nothing else runs in it.
- **The same prompt every other agent gets.** One renderer produces the worker,
  master and chief-of-staff prompts; a BYOA harness reads the identical text.
- **Identity on every write.** Notes, messages, claims and reviews are attributed
  to `TICKET_AGENT`, and the harness never has to say who it is.
- **Messages and briefs.** `tickets inbox`, `tickets msg`, `tickets brief` work
  the same from any harness; a brief is included in the prompt file.
- **Review and merge.** `tickets review` pins a branch and sha; the master
  merges. A BYOA agent's work goes through the same gate.
- **A run cap.** `--run-timeout` (minutes) kills a run that does not finish; the
  log records `TIMEOUT`. Output is teed to the watch log and capped per run.
- **Backoff.** Consecutive non-zero exits back the poll interval off
  exponentially to 15 minutes, so a broken harness does not spin.

## What it does not

- **It never drives your agent's inner loop.** The runtime hands over a prompt
  and a directory and does not look inside. Reasoning, tool use, retries,
  context management and when to stop are entirely yours.
- **It does not parse your harness's output.** Only the exit code is read, and
  only for backoff and `harness check`. Everything the board learns, your agent
  told it by running `tickets`.
- **It does not sandbox your harness.** The command runs with the watcher's
  environment and your permissions. `--safe` only changes the *built-in* CLIs'
  permission flags; it cannot make a custom command safe.
- **It does not install hooks for you.** `tickets hooks` covers Claude, Codex
  and Cursor. A custom harness gets its board context from the prompt file --
  which is why the prompt is a file and not a hook.
- **It does not manage credentials.** API keys, logins and rate limits belong to
  the harness. When one runs out, `tickets limit` is how you tell the board.

## Trust boundary

`workforce.json` is writable by any agent on the board, and the command
template stored there is executed by the watcher through a shell. Whoever can
write the board can therefore choose what a spawned watcher runs. This is not
new with BYOA -- the pre-existing `--tool <executable>` form had the same
property -- but it is the reason the board directory should be treated as
trusted infrastructure, not as agent-writable scratch. Do not point a watcher
at a board you do not control.
