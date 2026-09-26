# Any-CEO connect (product flow)

> **Not a first read.** This page is the CEO-seat wiring for a board that
> already exists and already has a team on it. If you just installed `atm`,
> start at [first-run.md](first-run.md) instead. This page will not make
> sense before you have a board.


Connecting is joining **Atman**, not Claude, Cursor, Codex, or another
provider. Board identity is `atman-<seat>` (example `atman-ceo`). A CoS
seat staffs workers. Choose harnesses from `atm harness available`; this
file does not ban a provider.

This is the launch pillar. A new CEO types `atm connect` on a living
board and gets an executable path, in this order:

1. Catalog + usage
2. Attach the living board / objective (do not invent a new team)
3. Join as `atman-<seat>`
4. Announce the Atman role
5. Ask the operator for feedback
6. Tasks they can actually run: `atm graph` / `atm map`

CEO does not claim worker tickets. Do not `atm init` or `atm clear`
on a board you did not create.

The copy-paste runbook (`ceo-mac-runbook.md`) calls this CLI flow.

---

## PATH (do this first)

`~/.local/bin/tickets` has been a stale shim to
`atman/tickets.py`. A CEO who trusts PATH
`atm connect` will get the old worker loop.

Confirm:

```sh
atm self
# script: must be this checkout or a pinned release — not a stale shim
```

Recut after merge to origin/main:

```sh
TICKETS_PY=<repo>/tickets.py
cat > ~/.claude/tools/tickets.py <<EOF
#!/usr/bin/env python3
import os, sys
os.execv(sys.executable, [sys.executable, "$TICKETS_PY"] + sys.argv[1:])
EOF
chmod +x ~/.claude/tools/tickets.py
# ~/.local/bin/atm and ~/.local/bin/tickets already point at the launcher
atm self
atm connect
```

---

Remote sessions and reconnect are experimental and outside the supported
preview. The commands below enroll local CLI seats.

## Exact commands a new CEO runs

Living board. Example seat `atman-ceo` is master. Example CoS `cos`.
Cursor is one catalog harness, not a staffing rule.

```sh
export PATH="$HOME/.local/bin:$PATH"
export TICKETS_DIR=<board>
export TICKET_AGENT=atman-ceo
cd <repo>

atm self
atm connect
# 1 catalog+usage  2 living objective  3 join as atman-ceo
# 4 announce  5 ask for feedback  6 graph/map

atm harness available          # same catalog; usage is recorded limits
atm join atman-ceo --roles master --can own-machine,browser --cost high --persistent --wake-mode continuous --harness cursor
atm hooks cursor --agent atman-ceo
atm hooks codex --agent atman-ceo
atm master                 # atman-ceo already holds master; take only if vacant
atm inbox
atm objective              # attach; do not --set unless empty

atm msg --to everyone "atman-ceo is Atman CEO on this living board. CoS is cos. Integrating: cursor. @everyone"
atm master log "ceo onboard: seat=atman-ceo integrations=cursor"

# Ask the operator: what should change about this connect path?
atm msg --to cos "CEO atman-ceo onboarded. Operator feedback: <their answer>"

atm graph
atm map
```

If they add work, CoS/master uses `atm plan` with real `deps` / `--after`
edges — not one `atm create` per title.

Live authority is `atm master`, `atm role list`, the message board, and
this flow.

---

## Fresh / empty board

`atm connect` (no `--ceo`) still prints the T-778/T-780 new-board
script: name → integrations → announce → `atm plan`. Workers who need
the claim loop: `atm connect --worker`.

---

## Sound then dispatch

The board **is** the plan index. There is no `plans/drafts/next/open/done`
tree.

| Capture step | Atman |
|---|---|
| add a thought | `atm capture "thought"` (`lane=capture`) |
| write it | `atm sound T-id --notes "cause=...; change=...; proof=...; deps=none"` |
| dispatch | `atm dispatch T-id --to atman-api --harness cursor` |
| status | `atm plan-status` |
| sync a PR | `atm pr-sync`; a DIFFERENT seat records `atm accept <id> --sha <full 40-char sha> --notes "evidence"`, then master `atm done` |
| retro | `atm retro` (files a capture; does not edit briefs itself) |

CEO sounds, CoS dispatches. CEO does **not** `atm next`.

```sh
atm capture "foo returns 500; maybe last week's deploy"
atm sound T-NNN --notes "cause=last deploy; change=tighter foo client timeout; proof=pytest -q tests/foo; deps=none"
atm plan-status
# CoS, not the CEO:
atm dispatch T-NNN --to atman-foo --harness cursor
```

Gemini dispatch records `harness=gemini`; persist/hooks is the wake.
`TICKETS_HARNESS_FAIL` usage rows are still refused.

Workers still `atm next` (atomic claim), implement, `atm sync`,
`atm review T-id --notes "..." --pr N`. Coordinators do not write the
product code. `atm pr-sync` prints `ready to close` when the recorded PR
is merged and the pin is a trunk ancestor; it never self-dones.

On PR CI or a review comment: run `atm pr-sync T-id` (or `atm pr-sync`
for every IN REVIEW ticket). That bounces a `atm msg` to the owner when
the PR is still waiting.

`atm discard T-id --reason "..."` keeps abandoned work for `atm retro`.
