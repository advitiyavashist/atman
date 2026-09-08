"""Deterministic routing policy: who *may* take a ticket, and why not.

The order of the gates is not ours to choose. `MasterLease.routing_mode` in the
frozen contract says it: "Deterministic eligibility (dependencies, capabilities,
free capacity, file/worktree conflicts) always runs first; model suggestions may
only reorder among already-eligible candidates." This module is that first half,
and it is pure: it reads a snapshot and returns decisions. Nothing here writes,
so the policy can be tested without a lease, a server or a clock.

Two properties the sweep depends on:

* **Total determinism.** The same board produces the same decisions in the same
  order. Every sort key ends in an id so that ties cannot be broken by dict or
  row order, which is what makes "two competing masters agree" testable at all.
* **Every rejection carries a reason.** A ticket that is not routed produces a
  `Decision` explaining which gate stopped it, because `master_decisions` is the
  operator's only account of why the board is quiet.

Liveness is a fifth gate, listed first because it is cheapest and because the
other four are meaningless for an agent that cannot act. It reuses T-180's
`derive_agent_state` rather than defining a second liveness rule -- a sweep that
disagreed with the dashboard about who is offline would be worse than either
rule alone.
"""

from __future__ import annotations

import json
from collections import namedtuple

from ..server.views import derive_agent_state, latest_session
from ..storage import ids


def _lease_is_live(lease):
    """Present, not revoked, not expired -- `_require_live_lease`'s three tests."""
    if lease is None:
        return False
    if lease.get("revoked_at") is not None:
        return False
    expires_at = lease.get("expires_at")
    return expires_at is not None and expires_at > ids.now()

# Agent states that can be given new work.
#
# `working` belongs here: an agent holding one of two slots is exactly who
# should get the second, and capacity -- not liveness -- is the gate that says
# how many. Leaving it out silently capped every agent at one ticket no matter
# what `max_active_tickets` said, because `derive_agent_state` reports any agent
# with an active ticket as `working`.
#
# `awaiting-input` is deliberately excluded: it is live but blocked on a human,
# so routing to it queues work behind something the board cannot clear.
ASSIGNABLE_AGENT_STATES = ("connected", "idle", "working")

# Ticket states a reservation may target. Only `open` -- claiming a ticket that
# already has an owner is a takeover, and this loop does not do takeovers.
ROUTABLE_TICKET_STATE = "open"

# Reservation states that still hold capacity.
LIVE_ASSIGNMENT_STATE = "queued"

# Ticket states that count as an agent actively holding a ticket.
ACTIVE_TICKET_STATES = ("claimed", "review")


Decision = namedtuple("Decision", "ticket_id decision reason agent_id")
Candidate = namedtuple("Candidate", "agent_id reason active_tickets")


def _loads(value, default):
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class Snapshot:
    """One consistent read of everything the policy needs.

    Taken once per sweep so that every gate sees the same board. Reading each
    gate straight from SQL would let a ticket be claimed between the dependency
    check and the capacity check, and the sweep would reason about two different
    boards without noticing.
    """

    def __init__(self, store, project_id):
        self.store = store
        self.project_id = project_id
        conn = store.conn
        self.tickets = [dict(r) for r in conn.execute(
            "SELECT * FROM tickets WHERE project_id = ? ORDER BY created_at, id",
            (project_id,)).fetchall()]
        self.by_id = {t["id"]: t for t in self.tickets}
        self.agents = [dict(r) for r in conn.execute(
            "SELECT * FROM agents WHERE project_id = ? ORDER BY id",
            (project_id,)).fetchall()]
        self.dependencies = {}
        for row in conn.execute(
                "SELECT ticket_id, depends_on FROM ticket_dependencies"
                " WHERE project_id = ? ORDER BY ticket_id, depends_on",
                (project_id,)).fetchall():
            self.dependencies.setdefault(row["ticket_id"], []).append(
                row["depends_on"])
        self.reservations = [dict(r) for r in conn.execute(
            "SELECT * FROM assignments WHERE project_id = ? AND state = ?"
            " ORDER BY created_at, id", (project_id, LIVE_ASSIGNMENT_STATE)
        ).fetchall()]

        self._agent_state = {}
        self._live_session = {}
        for agent in self.agents:
            payload = dict(agent)
            payload["capabilities"] = _loads(agent.get("capabilities"), [])
            payload["hook_health"] = _loads(agent.get("hook_health"), {})
            payload["capacity"] = {
                "max_active_tickets": agent.get("max_active_tickets") or 1,
                "active_tickets": self.active_ticket_count(agent["id"]),
            }
            lease = latest_session(store, agent["id"])
            self._agent_state[agent["id"]] = derive_agent_state(
                store, payload, lease)
            self._live_session[agent["id"]] = _lease_is_live(lease)
            agent["capabilities"] = payload["capabilities"]

    def agent_state(self, agent_id):
        return self._agent_state.get(agent_id, "offline")

    def has_live_session(self, agent_id):
        """The same bar `POST /claim` applies, and for the same reason.

        `derive_agent_state` reports an enrolled-but-never-connected agent as
        `idle`, which is honest for a dashboard and wrong for routing: T-180's
        `_require_live_lease` will refuse that agent's claim, so a reservation
        made for it can only sit until it expires and then read as though the
        agent ignored its work. Routing to an agent that cannot claim is not a
        softer failure than not routing -- it is a quieter one.
        """
        return self._live_session.get(agent_id, False)

    def active_ticket_count(self, agent_id):
        """Tickets held plus reservations queued -- both consume capacity.

        Counting only claimed tickets would let one sweep reserve a second
        ticket for an agent that has not yet picked up the first, which is the
        same over-assignment the capacity limit exists to prevent.
        """
        held = sum(1 for t in self.tickets
                   if t.get("owner") == agent_id
                   and t["state"] in ACTIVE_TICKET_STATES)
        queued = sum(1 for r in self.reservations if r["agent_id"] == agent_id)
        return held + queued

    def reserved_ticket_ids(self):
        return {r["ticket_id"] for r in self.reservations}

    def files(self, ticket):
        return set(_loads(ticket.get("files"), []))

    def occupied_files(self, exclude_ticket_id):
        """Files spoken for by tickets someone is already working."""
        taken = {}
        for ticket in self.tickets:
            if ticket["id"] == exclude_ticket_id:
                continue
            if ticket["state"] not in ACTIVE_TICKET_STATES:
                continue
            for path in self.files(ticket):
                taken.setdefault(path, ticket["id"])
        for reservation in self.reservations:
            if reservation["ticket_id"] == exclude_ticket_id:
                continue
            other = self.by_id.get(reservation["ticket_id"])
            if other is None:
                continue
            for path in self.files(other):
                taken.setdefault(path, other["id"])
        return taken

    def occupied_worktrees(self):
        """worktree -> agent currently living in it.

        An agent's own worktree is occupied by that agent, and a ticket being
        worked names the worktree it is being worked in. Either is enough to
        make handing that worktree to somebody else a takeover.
        """
        taken = {}
        for agent in self.agents:
            if agent.get("worktree") and self.agent_state(agent["id"]) != "revoked":
                taken.setdefault(agent["worktree"], agent["id"])
        for ticket in self.tickets:
            if ticket["state"] in ACTIVE_TICKET_STATES and ticket.get("worktree"):
                taken.setdefault(ticket["worktree"], ticket.get("owner"))
        return taken


def unmet_dependencies(snapshot, ticket):
    """Dependencies that are not `done`, in a stable order.

    A dependency that names a ticket which does not exist counts as unmet. It is
    the safer reading: the alternative is routing work whose prerequisite the
    board cannot even see.
    """
    unmet = []
    for dep in snapshot.dependencies.get(ticket["id"], []):
        other = snapshot.by_id.get(dep)
        if other is None or other["state"] != "done":
            unmet.append(dep)
    return unmet


def routable_tickets(snapshot):
    """(ticket, decision_or_None) for every ticket, in deterministic order.

    Returns non-routable tickets too, with the reason, because the operator
    needs "nothing moved and here is why" more than a short list.
    """
    reserved = snapshot.reserved_ticket_ids()
    out = []
    for ticket in snapshot.tickets:
        tid = ticket["id"]
        if ticket["state"] != ROUTABLE_TICKET_STATE:
            out.append((ticket, None))          # not a candidate, not a story
            continue
        if ticket.get("owner"):
            # An open ticket with an owner is a board inconsistency, not work to
            # route. Taking it would be a silent takeover.
            out.append((ticket, Decision(
                tid, "skipped", "ticket is open but already owned by %s"
                % ticket["owner"], None)))
            continue
        if tid in reserved:
            out.append((ticket, Decision(
                tid, "skipped", "a reservation is already queued for this ticket",
                None)))
            continue
        unmet = unmet_dependencies(snapshot, ticket)
        if unmet:
            out.append((ticket, Decision(
                tid, "skipped", "waiting on %s" % ", ".join(unmet), None)))
            continue
        out.append((ticket, "eligible"))
    return out


def _capability_reason(ticket, agent):
    """Whether `agent` is allowed to take `ticket`, and the reason either way.

    The frozen `Ticket` has a `role` and no `needs`, and `Agent` has both a
    `role` and a `capabilities` list, so the rule is: a ticket with no role may
    go to anyone, and a ticket with a role needs an agent whose role matches or
    whose capabilities name it. `capabilities` is the broader of the two, which
    is what lets one agent cover several lanes without being renamed.
    """
    role = (ticket.get("role") or "").strip()
    if not role:
        return True, "no role required"
    if (agent.get("role") or "").strip() == role:
        return True, "role %s" % role
    if role in (agent.get("capabilities") or []):
        return True, "capability %s" % role
    return False, "does not cover role %s" % role


def eligible_agents(snapshot, ticket, plan_claimed_files=None,
                     plan_claimed_worktrees=None):
    """Candidates for one ticket, best first, with per-agent rejection reasons.

    Ordered by least loaded then by id. Least-loaded spreads work; the id
    tie-break is what makes the result reproducible, which two competing masters
    depend on to reach the same answer.

    `plan_claimed_files` / `plan_claimed_worktrees` are what an in-progress
    `plan()` pass has already decided to hand out -- the snapshot alone cannot
    see them, because they are not reservations yet. Without them, two open
    tickets that touch the same file (or name the same worktree) are each
    judged only against the snapshot and both come out eligible, the same
    hazard `plan()` already guards against for capacity.
    """
    candidates, rejected = [], []
    occupied_files = snapshot.occupied_files(ticket["id"])
    for path, holder in (plan_claimed_files or {}).items():
        occupied_files.setdefault(path, holder)
    occupied_worktrees = snapshot.occupied_worktrees()
    for worktree, holder in (plan_claimed_worktrees or {}).items():
        occupied_worktrees.setdefault(worktree, holder)
    wanted_files = snapshot.files(ticket)
    wanted_worktree = ticket.get("worktree")

    for agent in snapshot.agents:
        aid = agent["id"]
        state = snapshot.agent_state(aid)
        if state not in ASSIGNABLE_AGENT_STATES:
            rejected.append((aid, "agent is %s" % state))
            continue
        if not snapshot.has_live_session(aid):
            rejected.append((aid, "agent has no live session lease"))
            continue

        allowed, why = _capability_reason(ticket, agent)
        if not allowed:
            rejected.append((aid, why))
            continue

        active = snapshot.active_ticket_count(aid)
        limit = agent.get("max_active_tickets") or 1
        if active >= limit:
            rejected.append((aid, "at capacity (%d/%d)" % (active, limit)))
            continue

        clash = sorted(path for path in wanted_files if path in occupied_files)
        if clash:
            rejected.append((aid, "files held by %s: %s"
                             % (occupied_files[clash[0]], ", ".join(clash))))
            continue

        # A worktree occupied by somebody else is a takeover, and this loop does
        # not take over. Note it is checked per agent, not per ticket: handing
        # the ticket to the agent already in that worktree is not a takeover.
        holder = occupied_worktrees.get(wanted_worktree) if wanted_worktree else None
        if holder is not None and holder != aid:
            rejected.append((aid, "worktree %s is in use by %s"
                             % (wanted_worktree, holder)))
            continue

        candidates.append(Candidate(aid, why, active))

    candidates.sort(key=lambda c: (c.active_tickets, c.agent_id))
    rejected.sort()
    return candidates, rejected


def plan(snapshot):
    """The whole deterministic pass: an ordered list of Decisions.

    Assignments are *planned*, not written, and capacity is decremented as the
    plan is built so that one sweep cannot hand two tickets to an agent whose
    limit is one. Files and worktrees are claimed the same way: the snapshot
    only knows about tickets already claimed/reserved, so two still-open
    tickets touching the same file (or worktree) are invisible to each other
    unless this pass tracks its own decisions as it goes.
    """
    decisions = []
    consumed = {}
    claimed_files = {}
    claimed_worktrees = {}

    for ticket, verdict in routable_tickets(snapshot):
        if verdict is None:
            continue
        if isinstance(verdict, Decision):
            decisions.append(verdict)
            continue

        candidates, rejected = eligible_agents(
            snapshot, ticket, claimed_files, claimed_worktrees)
        candidates = [c for c in candidates
                      if c.active_tickets + consumed.get(c.agent_id, 0)
                      < (_limit(snapshot, c.agent_id))]
        if not candidates:
            why = "; ".join("%s: %s" % pair for pair in rejected) or \
                "no agents are enrolled on this board"
            decisions.append(Decision(ticket["id"], "no_eligible_agent", why, None))
            continue

        chosen = candidates[0]
        consumed[chosen.agent_id] = consumed.get(chosen.agent_id, 0) + 1
        for path in snapshot.files(ticket):
            claimed_files.setdefault(path, ticket["id"])
        if ticket.get("worktree"):
            claimed_worktrees.setdefault(ticket["worktree"], chosen.agent_id)
        decisions.append(Decision(
            ticket["id"], "assigned",
            "%s, %d of %d slots used" % (
                chosen.reason,
                chosen.active_tickets + consumed[chosen.agent_id],
                _limit(snapshot, chosen.agent_id)),
            chosen.agent_id))
    return decisions


def _limit(snapshot, agent_id):
    for agent in snapshot.agents:
        if agent["id"] == agent_id:
            return agent.get("max_active_tickets") or 1
    return 1
