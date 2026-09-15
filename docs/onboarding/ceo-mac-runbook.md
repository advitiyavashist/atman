# Any-CEO onboarding (local `tickets.py`)

Copy-paste. Do not guess folders. This is the T-790 path: `python3` on a
real `tickets.py`, never the PATH shim. Pick harnesses from
`atm harness available`.

**You are onboarding.** This is not a ticket claim.

## Folders

Set these to the local checkout and living board. Do not paste a machine path.

```sh
TICKETS_PY=<repo>/tickets.py
LIVING_BOARD=<board>
REPO=<repo>

t() { python3 "$TICKETS_PY" "$@"; }

# PATH `tickets` is a stale shim (~/.local/bin/tickets -> ~/.claude/tools/tickets.py).
# Confirm before any spawn:
ls -l ~/.local/bin/tickets
python3 "$TICKETS_PY" --version
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

t harness available
# Probe every catalog row. Ask which to use. Missing is a row. Do not spawn yet.

t join "$TICKET_AGENT" --roles master --persistent --wake-mode continuous --harness cursor
t hooks cursor --agent "$TICKET_AGENT" --worktree "$PWD"
t master take

t msg --to everyone "atman-ceo is onboarding. Integrating: cursor. Objective and tasks next. @everyone"
t master log "onboarding: name=atman-ceo integrations=cursor"

t objective --set "Team intro: onboarding, plan/graph, persist-to-review" \
  --exit "throwaway proof green; living board unchanged except this announce"

t plan <<'EOF'
[{"key":"probe","title":"Probe integrations","role":"docs","deps":[]},
 {"key":"plan","title":"Plan the graph with real deps","role":"docs","deps":["probe"]},
 {"key":"review","title":"Persist to a reviewable SHA","role":"docs","deps":["plan"]}]
EOF
t graph
t map

t spawn cursor-worker --roles backend --harness cursor --persist --wake-mode task-only

t master cos cos
t msg --to cos "CoS: staff from the catalog the operator chose."
```

Do **not** run one `atm create` per title. Edges must be `atm plan`
JSON `deps` (real `--after` links). Mid-run: `atm dep` / `atm create --blocks`.

---

## B. Throwaway-board dry proof (what T-790 automates)

Same verbs, disposable `.tickets`. Never point `TICKETS_DIR` at a shared
living board.

```sh
TICKETS_PY=/path/to/this/checkout/tickets.py
t() { python3 "$TICKETS_PY" "$@"; }
REPO=/tmp/t790-ceo-proof   # test uses pytest tmp_path instead
cd "$REPO"                 # git repo; t init
unset TICKETS_DIR          # resolve the board from cwd

export TICKET_AGENT=atman-ceo
t init
t join "$TICKET_AGENT" --roles master --persistent --wake-mode continuous --harness cursor
t hooks cursor --agent "$TICKET_AGENT" --worktree "$PWD"
t master take
t msg --to everyone "atman-ceo is onboarding. Integrating: cursor. Objective and tasks next. @everyone"
t objective --set "Dry CEO onboarding path" --exit "graph has real deps; CoS messaged"
t plan <<'EOF'
[{"key":"api","title":"Build REST API","role":"backend","deps":[]},
 {"key":"ui","title":"Build login UI","role":"frontend","deps":["api"]}]
EOF
t graph
t join cursor-worker --roles backend --harness cursor --wake-mode task-only
# Paid Cursor is not invoked: --exec is a dry runner (auth gate skipped).
t spawn cursor-worker --harness cursor --max-runs 1 \
  --exec '/usr/bin/true {prompt_file} {cwd} {agent}'
t spawn cursor-worker --stop
t master cos cos-cursor
t join cos-cursor --roles docs --harness cursor --wake-mode continuous
t msg --to cos-cursor "CoS: staff from the catalog the operator chose."
```

`atm graph` must show the UI ticket waiting on the API ticket.

---

## What this is not

T-778 already productized master onboarding (startup first, probe catalog).
T-780 teaches the plan/graph/follow-up loop. This file does not
replace either. It pins **portable folder placeholders** (`<repo>`, `<board>`)
and an example sequence a CEO can execute without guessing.

See [master-howto.md](master-howto.md) for the long form.
