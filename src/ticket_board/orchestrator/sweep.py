"""The master routing loop: one fenced, resumable, fully logged pass.

What this loop may do is deliberately small: queue reservations, expire the ones
that ran out, and record why. It does not merge, does not claim on an agent's
behalf, does not move a ticket's state and does not reassign a ticket that has
an owner. Those are the operations that would let an automated loop destroy work
while nobody is watching, and none of them is reachable from here -- not because
of a flag, but because the code to do them is not in this module.

Fencing is the other half. The sweep holds a lease `epoch`, and every write goes
through `server.master`, which re-checks that epoch inside the same transaction
as the write. A superseded master therefore cannot write even if it is still
running, still believes it holds the lease, and is midway through a pass: its
first write raises and the pass ends as `superseded`. That is why two masters
racing is safe without any lock of our own.

Resumability comes from `master_lease.last_sweep_at`/`next_sweep_at`. They are
the checkpoint: a master that restarts reads them to learn whether a sweep is
due, and the reservation TTL means anything the previous master queued and never
delivered expires on its own rather than pinning a ticket forever.
"""

from __future__ import annotations

from ..storage import ids
from ..storage.db import write_txn
from ..server import master as master_ops
from ..server.auth import in_seconds
from ..server.errors import MasterLeaseExpired, TicketVersionConflict, NotFound
from . import eligibility, recommend

DEFAULT_RESERVATION_TTL_SECONDS = master_ops.DEFAULT_ASSIGNMENT_TTL_SECONDS


class SweepResult:
    """What one pass did, in the words the operator will read."""

    def __init__(self, status, decisions=None, expired=0, notes=None, epoch=None):
        self.status = status              # swept | paused | superseded | not_due
        self.decisions = decisions or []
        self.expired = expired
        self.notes = notes or []
        self.epoch = epoch

    @property
    def assigned(self):
        return [d for d in self.decisions if d.decision == "assigned"]

    def __repr__(self):
        return "SweepResult(%s, %d decisions, %d expired)" % (
            self.status, len(self.decisions), self.expired)


class Sweeper:
    """A master's routing loop, bound to one lease epoch.

    The epoch is passed in rather than read, and never refreshed by this object.
    That is on purpose: an object that re-read the epoch would quietly promote
    itself back to current after being superseded, which is the exact failure
    the fence exists to prevent. Losing the lease means constructing a new
    Sweeper, which means going through `take_lease` again.
    """

    def __init__(self, store, project_id, actor, lease_epoch, *,
                 recommender=None,
                 reservation_ttl_seconds=DEFAULT_RESERVATION_TTL_SECONDS):
        self.store = store
        self.project_id = project_id
        self.actor = actor
        self.lease_epoch = lease_epoch
        self.recommender = recommender
        self.reservation_ttl_seconds = reservation_ttl_seconds

    # ------------------------------------------------------------------ state

    def _lease(self):
        return master_ops.lease_row(self.store, self.project_id)

    def is_current(self):
        return master_ops.current_epoch(self.store, self.project_id) == self.lease_epoch

    def is_paused(self):
        row = self._lease()
        return bool(row["paused"]) if row is not None else False

    def is_due(self, now=None):
        """Whether the checkpoint says a sweep is owed.

        A master that restarts asks this instead of sweeping immediately, so a
        crash-loop cannot turn into a routing storm.
        """
        row = self._lease()
        if row is None:
            return False
        due = row["next_sweep_at"]
        return due is None or due <= (now or ids.now())

    # ------------------------------------------------------------------- pass

    def run_once(self, *, force=False):
        """One pass. The only public way to route.

        `force` skips the due-check for an operator pressing "run once"; it does
        not skip the fence or the pause, because neither is a schedule.
        """
        if not self.is_current():
            return SweepResult("superseded", epoch=self.lease_epoch)
        if self.is_paused():
            return SweepResult("paused", epoch=self.lease_epoch)
        if not force and not self.is_due():
            return SweepResult("not_due", epoch=self.lease_epoch)

        notes = []
        try:
            expired = self.expire_reservations()
        except MasterLeaseExpired:
            return SweepResult("superseded", epoch=self.lease_epoch)

        snapshot = eligibility.Snapshot(self.store, self.project_id)
        planned = eligibility.plan(snapshot)
        planned, suggestion_notes = self._apply_suggestions(snapshot, planned)
        notes.extend(suggestion_notes)

        recorded = []
        for decision in planned:
            try:
                recorded.append(self._commit(snapshot, decision))
            except MasterLeaseExpired:
                # Superseded mid-pass. Everything already written was written
                # under a lease we genuinely held, so it stands; we simply stop.
                return SweepResult("superseded", decisions=recorded,
                                   expired=expired, notes=notes,
                                   epoch=self.lease_epoch)
            except (TicketVersionConflict, NotFound) as error:
                # The board moved while we were deciding -- somebody claimed the
                # ticket, or an agent was removed. Not our error to raise: log it
                # and let the next pass see the new board.
                notes.append("%s: %s" % (decision.ticket_id, error.__class__.__name__))
                recorded.append(decision._replace(
                    decision="skipped", agent_id=None,
                    reason="board changed during the sweep (%s)"
                           % error.__class__.__name__))
                self._log(decision.ticket_id, "skipped",
                          "board changed during the sweep (%s)"
                          % error.__class__.__name__, None)

        self.checkpoint()
        return SweepResult("swept", decisions=recorded, expired=expired,
                           notes=notes, epoch=self.lease_epoch)

    def _apply_suggestions(self, snapshot, planned):
        """Let a recommender reorder, then re-decide only what it reordered.

        Runs on the eligible set the deterministic pass produced, so a
        suggestion can change *which* eligible agent gets a ticket and nothing
        else. Skips and no-eligible-agent decisions are not offered to it at
        all -- there is nothing there to reorder.
        """
        if self.recommender is None:
            return planned, []
        row = self._lease()
        mode = row["routing_mode"] if row is not None else "deterministic"
        if mode != "deterministic_plus_suggestions":
            return planned, ["recommender present but routing_mode is %s;"
                             " suggestions not consulted" % mode]

        notes, out = [], []
        for decision in planned:
            if decision.decision != "assigned":
                out.append(decision)
                continue
            ticket = snapshot.by_id[decision.ticket_id]
            candidates, _ = eligibility.eligible_agents(snapshot, ticket)
            suggested, call_notes = recommend.call(
                self.recommender, ticket, candidates)
            notes.extend(call_notes)
            ordered, apply_notes = recommend.apply(
                candidates, suggested, ticket_id=decision.ticket_id)
            notes.extend(apply_notes)
            if not ordered or ordered[0].agent_id == decision.agent_id:
                out.append(decision)
                continue
            chosen = ordered[0]
            out.append(decision._replace(
                agent_id=chosen.agent_id,
                reason="%s (suggested over %s)" % (chosen.reason,
                                                   decision.agent_id)))
        return out, notes

    # ------------------------------------------------------------------ writes

    def _commit(self, snapshot, decision):
        if decision.decision != "assigned":
            self._log(decision.ticket_id, decision.decision, decision.reason,
                      decision.agent_id)
            return decision
        ticket = snapshot.by_id[decision.ticket_id]
        master_ops.create_assignment(
            self.store, self.project_id, self.actor,
            ticket_id=decision.ticket_id, agent_id=decision.agent_id,
            reason=decision.reason, lease_epoch=self.lease_epoch,
            expected_version=ticket["version"],
            ttl_seconds=self.reservation_ttl_seconds)
        return decision

    def _log(self, ticket_id, kind, reason, agent_id):
        """Record a non-assignment decision.

        `create_assignment` already writes the `assigned` rows, so this only
        covers the ones that explain inaction -- which are the rows an operator
        looking at a stalled board actually needs.
        """
        with write_txn(self.store.conn) as conn:
            if _epoch(conn, self.project_id) != self.lease_epoch:
                raise MasterLeaseExpired(self.lease_epoch,
                                         _epoch(conn, self.project_id))
            conn.execute(
                "INSERT INTO master_decisions (project_id, ticket_id, decision,"
                " reason, agent_id, decided_at) VALUES (?, ?, ?, ?, ?, ?)",
                (self.project_id, ticket_id, kind, reason, agent_id, ids.now()))

    def expire_reservations(self):
        """Expire elapsed reservations, with an audit row.

        `server.master.expire_assignments` deliberately writes no audit row: it
        runs on GET, where there is no actor and where writing one would make a
        read generate stream events. Here there is a real actor holding a real
        lease, so the expiry is attributable and gets recorded -- which is what
        the T-180 handoff said this ticket should pick up.
        """
        now = ids.now()
        with write_txn(self.store.conn) as conn:
            existing = _epoch(conn, self.project_id)
            if existing != self.lease_epoch:
                raise MasterLeaseExpired(self.lease_epoch, existing)
            rows = conn.execute(
                "SELECT id, ticket_id, agent_id FROM assignments"
                " WHERE project_id = ? AND state = 'queued' AND expires_at <= ?"
                " ORDER BY id", (self.project_id, now)).fetchall()
            if not rows:
                return 0
            conn.execute(
                "UPDATE assignments SET state = 'expired', version = version + 1"
                " WHERE project_id = ? AND state = 'queued' AND expires_at <= ?",
                (self.project_id, now))
            for row in rows:
                self.store._audit(
                    conn, self.project_id, self.actor, "assignment.expire",
                    subject_type="assignment", subject_id=row["id"],
                    summary="reservation of %s for %s expired unclaimed"
                            % (row["ticket_id"], row["agent_id"]))
                conn.execute(
                    "INSERT INTO master_decisions (project_id, ticket_id,"
                    " decision, reason, agent_id, decided_at)"
                    " VALUES (?, ?, 'skipped', ?, ?, ?)",
                    (self.project_id, row["ticket_id"],
                     "reservation expired unclaimed", row["agent_id"], now))
            return len(rows)

    def checkpoint(self, *, now=None):
        """Record that a sweep happened and when the next one is owed.

        Fenced like every other write: a superseded master must not be able to
        move the schedule out from under the master that replaced it.
        """
        now = now or ids.now()
        with write_txn(self.store.conn) as conn:
            row = conn.execute(
                "SELECT epoch, sweep_interval_seconds FROM master_lease"
                " WHERE project_id = ?", (self.project_id,)).fetchone()
            existing = row["epoch"] if row is not None else 0
            if existing != self.lease_epoch:
                raise MasterLeaseExpired(self.lease_epoch, existing)
            interval = row["sweep_interval_seconds"] or \
                master_ops.SWEEP_INTERVAL_SECONDS
            conn.execute(
                "UPDATE master_lease SET last_sweep_at = ?, next_sweep_at = ?"
                " WHERE project_id = ?",
                (now, in_seconds(interval), self.project_id))


def _epoch(conn, project_id):
    row = conn.execute("SELECT epoch FROM master_lease WHERE project_id = ?",
                       (project_id,)).fetchone()
    return row["epoch"] if row is not None else 0
