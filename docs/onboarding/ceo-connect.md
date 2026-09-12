# Any-CEO connect — sound then dispatch

Connecting is joining **Atman**, not the provider. Identity is `atman-<seat>`.
CoS is `cursor` and staffs. CEO does **not** `tickets next`. Do not
`tickets init` or `tickets clear` on the living Steer board. HOLD T-773 and
T-774.

The board **is** the plan index. There is no `plans/drafts/next/open/done`
tree. Fatih’s loop maps onto tickets:

| Fatih | Atman |
|---|---|
| `/plan-add` | `tickets capture "thought"` (`lane=capture`) |
| `/plan-write` | `tickets sound T-id --notes "cause=...; change=...; proof=...; deps=none"` |
| `/plan-dispatch` | `tickets dispatch T-id --to atman-api --harness cursor` |
| `/plan-status` | `tickets plan-status` |
| `/plan-sync` | `tickets pr-sync` then master `tickets done` |
| `/plan-retro` | `tickets retro` (files a capture; does not edit briefs itself) |

## CEO sounds, CoS dispatches

```sh
export PATH="$HOME/.local/bin:$PATH"
export TICKETS_DIR=/Users/kavana/Downloads/steer/.tickets
export TICKET_AGENT=atman-ceo

tickets capture "foo returns 500; maybe last week's deploy"
tickets sound T-NNN --notes "cause=last deploy; change=tighter foo client timeout; proof=pytest -q tests/foo; deps=none"
tickets plan-status
# CoS, not the CEO:
tickets dispatch T-NNN --to atman-foo --harness cursor
```

Cursor-only spawns unless the operator chose another harness. `tickets harness
available` FAIL rows (Gemini list-only; usage FAIL) are refused by dispatch.

Workers still `tickets next` (atomic claim), implement, `tickets sync`,
`tickets review T-id --notes "..." --pr N`. Coordinators do not write the
product code. `tickets pr-sync` prints `ready to close` when the recorded PR
is merged and the pin is a trunk ancestor; it never self-dones.

## Subscriptions (first cut)

There is no second Project-inbox product. Mail is `tickets msg`. CoS/master
wake already exists via `tickets watch` / `tickets drive` and harness hooks.

On PR CI or a review comment: run `tickets pr-sync T-id` (or `tickets pr-sync`
for every IN REVIEW ticket). That bounces a `tickets msg` to the owner when
the PR is still waiting. Optional later: Cursor Project subscriptions on the
worker PR so this command runs without polling.

`tickets discard T-id --reason "..."` keeps abandoned work for `tickets retro`.
