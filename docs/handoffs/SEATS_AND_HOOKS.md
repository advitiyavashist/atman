# Seats, repositories and communication

User-approved responsibility split: Opus CEO, Sonnet CoS, Sol/Codex planner/assigner/high-level reviewer. This packet records intended seats; it does not claim the new Claude sessions exist or are reachable.

| Seat | Starting unique board identity | Durable role |
|---|---|---|
| Opus CEO | `atman-ceo-opus-0913` | `steer.ceo` and technical desk `steer.cto` |
| Sonnet CoS | `atman-cos-sonnet-0913` | `steer.chief-of-staff` |
| Sol planner | existing `atman-ceo-codex-0912` | `steer.planner` |

The planner's legacy ID does not confer CEO authority. Keep it for this running session so pending messages reach it; a replacement planner chooses a fresh ID. At packet creation the live CEO/CTO/master still pointed to that ID and CoS to `cursor`; new seats must explicitly activate from their own sessions. Preserve delivery continuity until takeover ACK, then retire the old CoS watcher. Do not register another agent from your cwd.

Repos: `/Users/kavana/Downloads/atman` -> `advitiyavashist/atman`; `/Users/kavana/Downloads/steer` -> `advitiyavashist/steer`. Shared board for both: `/Users/kavana/Downloads/steer/.tickets`. Never initialize a second board or edit its JSON. Ticket repo evidence and deliverable origin matter more than folder names.

Current CLI: `/Users/kavana/.local/bin/tickets` -> `/Users/kavana/Downloads/atman/.worktrees/atman-runtime-current/tickets.py`, snapshot `3679053`. `atm` rename is T-809 IN REVIEW; use the existing `tickets` or explicit runtime script until its actual install is proven. Update the clean runtime only after approved, tested integration. Never blindly use `tickets merge`: its integration cleanup can delete branches; preserve all foreign branches.

## Start each Claude seat independently

First create a separate Atman worktree/branch from fetched `origin/main`, for example `opus-ceo-0913` and `sonnet-cos-0913`. Do not copy another seat's `.claude` files, provider session variables or board hooks. Launch a fresh Claude terminal with the intended model and cleared inherited parent provider identities; do not reuse a Codex/Cursor parent's session. Verify installed Claude model aliases/account access rather than assuming Fable's CLI name.

In each new session's own worktree, substitute its identity and roles:

```sh
export TICKETS_DIR=/Users/kavana/Downloads/steer/.tickets
export TICKET_AGENT=atman-ceo-opus-0913
export TICKET_SEAT="$TICKET_AGENT"
tickets join "$TICKET_AGENT" --roles master,review --can own-machine,network --harness claude --model opus --lifecycle persistent --wake-mode continuous
tickets hooks claude --agent "$TICKET_AGENT" --settings "$PWD/.claude/settings.json"
tickets self
tickets inbox
```

Sonnet uses `atman-cos-sonnet-0913`, `--roles cos,ops,review`, `--model sonnet`; register browser capability only if actually available. Validate both project settings and any effective global/local Claude settings for stale board commands. Preserve custom hooks/permissions. The T-839 corrected code removes parent board-hook inheritance; do not claim its protection is live before activating that reviewed runtime.

Install hooks before a new/reloaded Claude session so it reads them. The generated SessionStart hook brings board context, UserPromptSubmit checks inbox, and Stop keeps work going while actionable board work remains. These lifecycle hooks do not by themselves wake an idle session from a new message.

For native persistence, run `tickets join "$TICKET_AGENT" --persistent --harness claude --model opus --wake-mode continuous` inside the actual interactive session (Sonnet substitutes its model). Registration needs a real supported transport, documented locally as `CLAUDE_CODE_MESSAGING_SOCKET`; do not invent a socket path/token/env value. `tickets self` must report what is actually registered. If unavailable, remain persistent-but-offline and identify the supervised/adapter setup needed; do not silently substitute another harness or start a second copy of the session.

Use [native session adapter evidence](../t683-native-session-adapters.md), [hook identities](../hook-identities.md), and [BYOA](../byoa.md). Those documents include fixture/protocol proofs, not a blanket real interactive Claude wake guarantee. A remote/Grok seat needs its fenced bridge and corrected unique wrapper; hooks alone do not connect a remote service.

## Activate authority after self-registration

Opus, from its own seat:

```sh
tickets role take steer.ceo --expected-holder atman-ceo-codex-0912 --reason "User appoints Opus CEO; Sol becomes planner" --context docs/handoffs/OPUS_CEO.md
tickets role take steer.cto --expected-holder atman-ceo-codex-0912 --reason "User assigns executive technical desk to Opus" --context docs/handoffs/OPUS_CEO.md
tickets master take
```

Sonnet, from its own seat:

```sh
tickets role take steer.chief-of-staff --expected-holder cursor --reason "User appoints Sonnet CoS" --context docs/handoffs/SONNET_COS.md
tickets master cos "$TICKET_AGENT"
```

If handoff files are not on your new branch yet, pass their actual readable absolute path from `planner-handoffs-t849` as `--context`. If an expected holder differs, inspect role history and coordinate; never force a stale takeover. Role ownership is coordination, not an authentication boundary. Confirm `tickets role list`, master brief and correct `tickets here` location, then announce activation. Do not claim implementation under either leadership seat.

## Board messages and wake acceptance

```sh
tickets msg "Decision needed: exact option, evidence and next action" --to atman-ceo-opus-0913 --re T-839
tickets msg "Please unblock: concrete blocker and recovery action" --to atman-cos-sonnet-0913 --re T-811
tickets msg "Planning request: objective, constraints and acceptance gap" --to atman-ceo-codex-0912 --re T-810
```

Send one recipient per command. Replies name the original ticket/message; do not reply-loop an ACK. Read available messages in a batch and consult live ticket status before acting. Workers use `tickets update` for milestone evidence and `tickets review --notes ... --pr N` for exact deliverables. Master/CoS do not buy model turns just to report unchanged status. Regular heartbeat/progress obligations still apply during active work.

Acceptance probe: CEO sends CoS a unique nonce and asks for a board ACK plus one concrete current blocker/action; CoS returns a different nonce to CEO; each sends planner a single task-relevant probe. Capture message ID, sender/recipient seat, transport receipt, actual turn start, board response, next work action, latency and whether Enter/operator intervention was needed. Confirm inbox separation and unchanged other seat/worktree. `sent`, `queued`, `inbox-posted`, `watch-poked` and displayed context are not equivalent to actual `woken`/work-started. If transport fails, preserve durable messages and report exact auth/offline/permission cause. No fabricated live acceptance.

## Current approval and efficiency rules

Review [coordination and success](../onboarding/coordination-and-success.md), committed separately at `f430403` / T-848 IN REVIEW; read its local worktree file if not merged yet. Batch stale packets, reuse correction/verification tickets, one review/merge executor, accept exact work rather than merge counts. Cost includes retries/review/coordination; unknown cost stays null. T-811/T-831 must validate the scorecard before publication.
