# Any-CEO onboarding on this Mac

Copy-paste. Do not guess folders. This is the T-790 path: Cursor harness
only, `python3` on a real `tickets.py`, never the PATH shim.

**You are onboarding.** This is not a ticket claim.

## Folders (this machine)

```sh
TICKETS_PY=/Users/kavana/Downloads/atman/.worktrees/cursor-community-t790/tickets.py
# After this branch merges, prefer local main:
# TICKETS_PY=/Users/kavana/Downloads/atman/.worktrees/master-merge/tickets.py
LIVING_BOARD=/Users/kavana/Downloads/steer/.tickets
STEER=/Users/kavana/Downloads/steer
ATMAN=/Users/kavana/Downloads/atman

t() { python3 "$TICKETS_PY" "$@"; }

# PATH `tickets` is a stale shim (~/.local/bin/tickets -> ~/.claude/tools/tickets.py).
# Confirm before any spawn:
ls -l ~/.local/bin/tickets
python3 "$TICKETS_PY" --version
```

Do not run `tickets init` or `tickets clear` on the living Steer board.
Do not work on steer `main`. HOLD T-773 and T-774. No NER flip. No T-095 /
T-138. Spawn **cursor** only unless the operator names another harness.

`join` uses `--persistent` (seat lifecycle). `spawn --persist` is the watcher
loop. The ticket’s `--persist` on join means `--persistent`.

---

## A. Living Steer board (operator CEO — not this ticket’s proof)

Bind the existing board. Do not create a second one.

```sh
export TICKET_AGENT=atman-ceo
export TICKETS_DIR=/Users/kavana/Downloads/steer/.tickets
cd /Users/kavana/Downloads/atman   # or a dedicated CEO worktree; never steer main

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

# Cursor only
t spawn cursor-worker --roles backend --harness cursor --persist --wake-mode task-only

t master cos cursor
t msg --to cursor "CoS: staff cursor seats only. HOLD T-773 T-774. No live plan dump beyond operator answers."
```

Do **not** run one `tickets create` per title. Edges must be `tickets plan`
JSON `deps` (real `--after` links). Mid-run: `tickets dep` / `tickets create --blocks`.

---

## B. Throwaway-board dry proof (what T-790 automates)

Same verbs, disposable `.tickets`. Never point `TICKETS_DIR` at Steer.

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
t msg --to cos-cursor "CoS: staff cursor only."
```

`tickets graph` must show the UI ticket waiting on the API ticket.

---

## What this is not

T-778 already productized master onboarding (startup first, probe catalog).
T-780 (IN REVIEW) teaches the plan/graph/follow-up loop. This file does not
replace either. It pins **this Mac’s folders** and a Cursor-only sequence a
CEO can execute without guessing.

See [master-howto.md](master-howto.md) for the long form.
