# Master routing loop (T-182) — handoff

Written for T-184 (the console that renders this), T-188 (the runner that wakes
an agent when a reservation lands) and T-185 (which has to verify it). It assumes
`docs/api-notes.md`; it does not assume you have read the design docs.

## What the loop is allowed to do

Four writes, and no others:

| Write | Where |
|---|---|
| queue a reservation | `server.master.create_assignment` |
| expire an elapsed reservation, with an audit row | `Sweeper.expire_reservations` |
| record why a ticket did **not** move | `master_decisions` |
| move the sweep checkpoint | `master_lease.last_sweep_at` / `next_sweep_at` |

It does **not** merge, claim on an agent's behalf, change a ticket's state or
owner, or reassign a ticket that has one. Those are the operations that would let
an unattended loop destroy work, and none of them is reachable from
`orchestrator/` — not behind a flag, the code is not there. A reservation is an
*offer*; the agent still has to `POST /claim` it.

## Eligibility, in the contract's order

`MasterLease.routing_mode` specifies it: "Deterministic eligibility
(dependencies, capabilities, free capacity, file/worktree conflicts) always runs
first; model suggestions may only reorder among already-eligible candidates."

0. **Live.** Derived state in `connected|idle|working`, *and* a session lease
   that exists, is unrevoked and unexpired — the same three tests
   `POST /claim` applies in `_require_live_lease`. This gate is not in the
   contract's list because it is not a policy choice: reserving for an agent
   the API would refuse produces a reservation nobody can take, which expires
   and reads as though the agent ignored its work.
   `awaiting-input` is excluded (live, but blocked on a human).
   `working` is **included** — see the trap below.
1. **Dependencies.** Every `depends_on` must be `done`. A dependency naming a
   ticket that does not exist counts as unmet.
2. **Capabilities.** A ticket with no `role` may go to anyone. A ticket with a
   role needs an agent whose `role` matches or whose `capabilities` name it.
   (The frozen `Ticket` has no `needs` field; `capabilities` is the broader of
   the two and is what lets one agent cover several lanes.)
3. **Capacity.** Held tickets **plus queued reservations** must be under
   `max_active_tickets`. Counting only claimed tickets lets a second sweep
   reserve again before the first reservation is picked up.
4. **Files / worktrees.** A ticket whose `files` overlap an in-flight ticket's
   is not routed. A ticket whose `worktree` is occupied may go only to the agent
   already in it — anyone else would be a takeover.

Ties break on agent id after "least loaded", so the plan is **totally
deterministic**: the same board yields the same decisions in the same order.
That is what lets two masters agree, and what makes the loop testable.

### The trap worth knowing

`derive_agent_state` reports *any* agent holding a ticket as `working`. Leaving
`working` out of the assignable set caps every agent at one ticket regardless of
`max_active_tickets` — and it does so invisibly, because within a single sweep
the snapshot is taken while the agent is still `idle`. It passes tests and fails
in production. There is a named regression test for it.

## Fencing

The `Sweeper` is bound to one lease `epoch` and **never re-reads it**. Every
write re-checks that epoch inside its own transaction, so a superseded master
cannot write even if it is still running and still believes it holds the lease:
its first write raises and the pass ends with `status="superseded"`. A `Sweeper`
that has lost the lease stays dead even if its holder later takes the lease
again — recovering means constructing a new one, which means going through
`take_lease`.

Statuses: `swept`, `paused`, `not_due`, `superseded`.

## Recovery

`last_sweep_at` / `next_sweep_at` are the checkpoint. A restarted master asks
`is_due()` rather than sweeping immediately, so a crash-loop cannot become a
routing storm. Anything the previous master queued and never delivered expires
on its own via the reservation TTL, so a dead master cannot pin a ticket.

`run_once(force=True)` is the operator's "run once": it overrides the *schedule*
only. Pause and the epoch fence are authority, not schedule, and `force` does
not touch either.

## Model suggestions

Only in `routing_mode: deterministic_plus_suggestions`, and `routing_mode` is
server state, not a caller's flag. A recommender is `f(ticket, [agent_id]) ->
[agent_id]`, offered **only** tickets the deterministic pass already marked
`assigned`, and its output is validated rather than trusted: entries that are not
already-eligible candidates are dropped, duplicates and non-strings are dropped,
a recommender that raises is ignored, and anything unmentioned keeps its
deterministic position. The worst a broken or hostile recommender can do is
reorder an approved list — or be ignored — and every departure is recorded in
`SweepResult.notes`.

## For T-188

A queued reservation is the wake signal. It carries `ticket_id`, `agent_id`,
`expires_at` and the `lease_epoch` that created it. Waking on a reservation whose
`lease_epoch` is behind the current one means the master that offered it has been
replaced — re-read before acting.

## Honest limits

Correctness on one machine, in one process, over SQLite. Nothing here is
measured under load and no latency number appears in this lane. The competing-
master tests race two `Sweeper` objects against one store, which is the fence's
real failure mode (two live masters), but not two OS processes — that belongs to
T-185 against a deployed board. There is no scheduler: something must call
`run_once`; this lane provides the decision and the fence, not a daemon.
