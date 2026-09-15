# Any-CEO onboarding

For a first board, start with [first-run.md](first-run.md).

Install `atm` from your checkout and confirm it with `atm self`. Pick harnesses
from `atm harness available`.

**You are onboarding.** This is not a ticket claim.

## Folders

Set these to the local checkout and living board. Do not paste a machine path.

```sh
LIVING_BOARD=<board>
REPO=<repo>

# PATH `tickets` is a stale shim (~/.local/bin/tickets -> ~/.claude/tools/tickets.py).
# Confirm before any spawn:
atm self
atm --version
```

Do not run `atm init` or `atm clear` on a living board you did not create.
Do not work on `main`. `join` uses `--persistent` (seat lifecycle).
`spawn --persist` is the watcher loop. The ticket’s `--persist` on join
means `--persistent`.

---

## A. Living board (operator CEO — not this ticket’s proof)

Bind the existing board. Do not create a second one.

```sh
export TICKET_AGENT=atman-ceo
export TICKETS_DIR="$LIVING_BOARD"
cd "$REPO"   # or a dedicated CEO worktree; never main

atm harness available
# Probe every catalog row. Ask which to use. Missing is a row. Do not spawn yet.

atm join "$TICKET_AGENT" --roles master --persistent --wake-mode continuous --harness cursor
atm hooks cursor --agent "$TICKET_AGENT" --worktree "$PWD"
atm master take

atm msg --to everyone "atman-ceo is onboarding. Integrating: cursor. Objective and tasks next. @everyone"
atm master log "onboarding: name=atman-ceo integrations=cursor"

atm objective --set "Team intro: onboarding, plan/graph, persist-to-review" \
  --exit "throwaway proof green; living board unchanged except this announce"

atm plan <<'EOF'
[{"key":"probe","title":"Probe integrations","role":"docs","deps":[],
  "cause":"we do not know what can be spent","change":"run harness available","proof":"every catalog row printed"},
 {"key":"plan","title":"Plan the graph with real deps","role":"docs","deps":["probe"],
  "cause":"probe answered","change":"write the plan JSON with deps","proof":"atm graph shows the edges"},
 {"key":"review","title":"Persist to a reviewable SHA","role":"docs","deps":["plan"],
  "cause":"graph is staffed","change":"work a ticket to a branch@sha","proof":"atm review records branch@sha"}]
EOF
atm graph
atm map

atm spawn cursor-worker --roles backend --harness cursor --persist --wake-mode task-only

atm master cos cos
atm msg --to cos "CoS: staff from the catalog the operator chose."
```

Do **not** run one `atm create` per title. Edges must be `atm plan`
JSON `deps` (real `--after` links). Mid-run: `atm dep` / `atm create --blocks`.

---

## B. Throwaway-board dry proof (what T-790 automates)

Same verbs, disposable `.tickets`. Never point `TICKETS_DIR` at a shared
living board.

```sh
REPO=/tmp/t790-ceo-proof   # test uses pytest tmp_path instead
cd "$REPO"                 # existing Git repo
unset TICKETS_DIR          # resolve the board from cwd

export TICKET_AGENT=atman-ceo
atm init
atm join "$TICKET_AGENT" --roles master --persistent --wake-mode continuous --harness cursor
atm hooks cursor --agent "$TICKET_AGENT" --worktree "$PWD"
atm master take
atm msg --to everyone "atman-ceo is onboarding. Integrating: cursor. Objective and tasks next. @everyone"
atm objective --set "Dry CEO onboarding path" --exit "graph has real deps; CoS messaged"
atm plan <<'EOF'
[{"key":"api","title":"Build REST API","role":"backend","deps":[],
  "cause":"clients have nothing to call","change":"REST API for the model","proof":"pytest -q tests/api"},
 {"key":"ui","title":"Build login UI","role":"frontend","deps":["api"],
  "cause":"API is live","change":"login screen on the API","proof":"login returns a session"}]
EOF
atm graph
atm join cursor-worker --roles backend --harness cursor --wake-mode task-only
# Paid Cursor is not invoked: --exec is a dry runner (auth gate skipped).
atm spawn cursor-worker --harness cursor --max-runs 1 \
  --exec '/usr/bin/true {prompt_file} {cwd} {agent}'
atm spawn cursor-worker --stop
atm join cos-cursor --roles docs --harness cursor --wake-mode continuous
atm master cos cos-cursor
atm msg --to cos-cursor "CoS: staff from the catalog the operator chose."
```

`atm graph` must show the UI ticket waiting on the API ticket.

---

## What this is not

T-778 already productized master onboarding (startup first, probe catalog).
T-780 teaches the plan/graph/follow-up loop. This file does not
replace either. It pins **portable folder placeholders** (`<repo>`, `<board>`)
and an example sequence a CEO can execute without guessing.

See [master-howto.md](master-howto.md) for the long form.

## Run from the checkout, not a stale shim

If `atm self` shows an unexpected file, run the checkout directly:

```sh
TICKETS_PY=<repo>/tickets.py
python3 "$TICKETS_PY" self
```
