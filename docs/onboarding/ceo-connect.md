# Any-CEO connect (product flow)

Connecting is joining **Atman**, not Claude, Cursor, Codex, or another
provider. Board identity is `atman-<seat>` (example `atman-ceo`). CoS is
`cursor` and staffs. Cursor-only spawns unless the operator says otherwise.

This is the launch pillar. A new CEO types `tickets connect` on a living
board and gets an executable path, in this order:

1. Catalog + usage
2. Attach the living board / objective (do not invent a new team)
3. Join as `atman-<seat>`
4. Announce the Atman role
5. Ask the operator for feedback
6. Tasks they can actually run: `tickets graph` / `tickets map`

CEO does not claim worker tickets. Do not `tickets init` or `tickets clear`.
HOLD T-773 and T-774.

T-790 owns the Mac copy-paste runbook (`ceo-mac-runbook.md`). This file is
the CLI product flow that runbook should call.

---

## PATH (do this first)

`~/.local/bin/tickets` has been a stale shim to
`atman/.worktrees/sol-agy-harness/tickets.py`. A CEO who trusts PATH
`tickets connect` will get the old worker loop.

Confirm:

```sh
tickets self
# script: must be this checkout or Atman origin/main after merge — not sol-agy-harness
```

Recut after merge to origin/main:

```sh
TICKETS_PY=/Users/kavana/Downloads/atman/tickets.py
cat > ~/.claude/tools/tickets.py <<EOF
#!/usr/bin/env python3
import os, sys
os.execv(sys.executable, [sys.executable, "$TICKETS_PY"] + sys.argv[1:])
EOF
chmod +x ~/.claude/tools/tickets.py
# ~/.local/bin/tickets already points at ~/.claude/tools/tickets.py
tickets self
tickets connect
```

---

## Exact commands a new CEO runs

Living Steer board. `atman-ceo` is master. CoS is `cursor`.

```sh
export PATH="$HOME/.local/bin:$PATH"
export TICKETS_DIR=/Users/kavana/Downloads/steer/.tickets
export TICKET_AGENT=atman-ceo
cd /Users/kavana/Downloads/steer

tickets self
tickets connect
# 1 catalog+usage  2 living objective  3 join as atman-ceo
# 4 announce  5 ask for feedback  6 graph/map

tickets harness available          # same catalog; usage is recorded limits
tickets join atman-ceo --roles master --can own-machine,browser --cost high --persistent --wake-mode continuous --harness cursor
tickets hooks cursor --agent atman-ceo
tickets hooks codex --agent atman-ceo     # mail follow-up is not Claude-only
tickets hooks remote --agent atman-ceo
tickets master                 # atman-ceo already holds master; take only if vacant
tickets inbox
tickets objective              # attach; do not --set unless empty

tickets msg --to everyone "atman-ceo is Atman CEO on this living board. CoS is cursor. Integrating: cursor. @everyone"
tickets master log "ceo onboard: seat=atman-ceo integrations=cursor"

# Ask the operator: what should change about this connect path?
tickets msg --to cursor "CEO atman-ceo onboarded. Operator feedback: <their answer>"

tickets graph
tickets map
```

If they add work, CoS/master uses `tickets plan` with real `deps` / `--after`
edges — not one `tickets create` per title.

HANDOVER 2026-09-08 is historical. Live authority is `tickets master`,
`tickets role list`, the message board, and this flow.

---

## Fresh / empty board

`tickets connect` (no `--ceo`) still prints the T-778/T-780 new-board
script: name → integrations → announce → `tickets plan`. Workers who need
the claim loop: `tickets connect --worker`.

---

## Sound then dispatch (Fatih loop on this board)

The board **is** the plan index. There is no `plans/drafts/next/open/done`
tree.

| Fatih | Atman |
|---|---|
| `/plan-add` | `tickets capture "thought"` (`lane=capture`) |
| `/plan-write` | `tickets sound T-id --notes "cause=...; change=...; proof=...; deps=none"` |
| `/plan-dispatch` | `tickets dispatch T-id --to atman-api --harness cursor` |
| `/plan-status` | `tickets plan-status` |
| `/plan-sync` | `tickets pr-sync` then master `tickets done` |
| `/plan-retro` | `tickets retro` (files a capture; does not edit briefs itself) |

CEO sounds, CoS dispatches. CEO does **not** `tickets next`.

```sh
tickets capture "foo returns 500; maybe last week's deploy"
tickets sound T-NNN --notes "cause=last deploy; change=tighter foo client timeout; proof=pytest -q tests/foo; deps=none"
tickets plan-status
# CoS, not the CEO:
tickets dispatch T-NNN --to atman-foo --harness cursor
```

Cursor-only product-spawns unless the operator chose another harness. Gemini
dispatch records `harness=gemini`; persist/hooks is the wake (no Gemini
product job). `TICKETS_HARNESS_FAIL` usage rows are still refused.

Workers still `tickets next` (atomic claim), implement, `tickets sync`,
`tickets review T-id --notes "..." --pr N`. Coordinators do not write the
product code. `tickets pr-sync` prints `ready to close` when the recorded PR
is merged and the pin is a trunk ancestor; it never self-dones.

On PR CI or a review comment: run `tickets pr-sync T-id` (or `tickets pr-sync`
for every IN REVIEW ticket). That bounces a `tickets msg` to the owner when
the PR is still waiting.

`tickets discard T-id --reason "..."` keeps abandoned work for `tickets retro`.
