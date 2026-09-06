"""Master lease and reservations: the CAS operations behind the master routes.

The `master_lease` and `assignments` tables are T-179's; the *sweep* that
decides who gets what is T-182's. What lives here is only the part the frozen
routes need: a compare-and-swap takeover with a fencing epoch, a pause toggle
fenced by that epoch, and a reservation that refuses a superseded master.

It is written in this package rather than added to `storage/board.py` because
another lane is editing that module right now; the coupling is the two private
helpers `_replay`/`_remember`, used so that a `request_id` replay behaves
identically here and in the store. `docs/api-notes.md` flags them as the seam
T-182 may want to promote into `BoardStore`.
"""

from __future__ import annotations

import json

from ..storage import ids
from ..storage.db import write_txn
from .auth import in_seconds
from .errors import (
    MasterLeaseConflict,
    MasterLeaseExpired,
    NotFound,
    TicketVersionConflict,
)

DEFAULT_LEASE_TTL_SECONDS = 120
DEFAULT_ASSIGNMENT_TTL_SECONDS = 600
SWEEP_INTERVAL_SECONDS = 30


def lease_row(store, project_id):
    return store.conn.execute(
        "SELECT * FROM master_lease WHERE project_id = ?", (project_id,)
    ).fetchone()


def current_epoch(store, project_id):
    row = lease_row(store, project_id)
    return row["epoch"] if row is not None else 0


def take_lease(store, project_id, holder, *, expected_epoch,
               ttl_seconds=DEFAULT_LEASE_TTL_SECONDS, request_id=None):
    """Compare-and-swap takeover.

    Two masters racing with the same `expected_epoch` cannot both win: the read
    and the write are inside one immediate transaction, so the loser sees the
    winner's epoch and gets 409 `master_lease_conflict`.

    Note what is deliberately *not* checked: whether the current lease has
    expired. Epoch is the fence. Requiring expiry as well would let a live but
    unresponsive master hold the board hostage, and permitting a takeover
    without the epoch would let two masters route at once.
    """
    body = {"expected_epoch": expected_epoch, "ttl_seconds": ttl_seconds,
            "holder": holder}
    with write_txn(store.conn) as conn:
        replay = store._replay(conn, project_id, request_id, "master.take_lease", body)
        if replay is not None:
            return replay
        row = conn.execute(
            "SELECT * FROM master_lease WHERE project_id = ?", (project_id,)
        ).fetchone()
        existing = row["epoch"] if row is not None else 0
        if expected_epoch != existing:
            raise MasterLeaseConflict(expected_epoch, existing)
        now = ids.now()
        expires_at = in_seconds(ttl_seconds)
        next_sweep = in_seconds(SWEEP_INTERVAL_SECONDS)
        epoch = existing + 1
        if row is None:
            conn.execute(
                "INSERT INTO master_lease (project_id, holder, epoch, acquired_at,"
                " expires_at, routing_mode, paused, next_sweep_at,"
                " sweep_interval_seconds) VALUES (?, ?, ?, ?, ?, 'deterministic',"
                " 0, ?, ?)",
                (project_id, json.dumps(holder), epoch, now, expires_at,
                 next_sweep, SWEEP_INTERVAL_SECONDS),
            )
        else:
            conn.execute(
                "UPDATE master_lease SET holder = ?, epoch = ?, acquired_at = ?,"
                " expires_at = ?, next_sweep_at = ? WHERE project_id = ?",
                (json.dumps(holder), epoch, now, expires_at, next_sweep, project_id),
            )
        store._audit(conn, project_id, holder, "master.lease.taken",
                     subject_type="master_lease", subject_id=str(epoch),
                     request_id=request_id,
                     summary="lease taken at epoch {}".format(epoch))
        result = _serialize(conn, project_id)
        store._remember(conn, project_id, request_id, "master.take_lease",
                        body, result)
    return result


def set_paused(store, project_id, actor, *, lease_epoch, paused, request_id=None):
    """Pause or resume sweeps. Fenced: a superseded epoch is 409, not a no-op."""
    body = {"lease_epoch": lease_epoch, "paused": paused}
    with write_txn(store.conn) as conn:
        replay = store._replay(conn, project_id, request_id, "master.pause", body)
        if replay is not None:
            return replay
        row = conn.execute(
            "SELECT * FROM master_lease WHERE project_id = ?", (project_id,)
        ).fetchone()
        existing = row["epoch"] if row is not None else 0
        if lease_epoch != existing:
            raise MasterLeaseExpired(lease_epoch, existing)
        conn.execute(
            "UPDATE master_lease SET paused = ? WHERE project_id = ?",
            (1 if paused else 0, project_id),
        )
        store._audit(conn, project_id, actor,
                     "master.paused" if paused else "master.resumed",
                     subject_type="master_lease", subject_id=str(existing),
                     request_id=request_id,
                     summary="sweeps {}".format("paused" if paused else "resumed"))
        result = _serialize(conn, project_id)
        store._remember(conn, project_id, request_id, "master.pause", body, result)
    return result


def create_assignment(store, project_id, actor, *, ticket_id, agent_id, reason,
                      lease_epoch, expected_version,
                      ttl_seconds=DEFAULT_ASSIGNMENT_TTL_SECONDS, request_id=None):
    """Queue a reservation. Requires the current epoch and the ticket's version.

    Both checks matter for different reasons: the epoch stops a superseded
    master from routing, and the version stops a reservation being made against
    a ticket that someone already claimed while the master was deciding.
    """
    body = {"ticket_id": ticket_id, "agent_id": agent_id, "reason": reason,
            "lease_epoch": lease_epoch, "expected_version": expected_version,
            "ttl_seconds": ttl_seconds}
    with write_txn(store.conn) as conn:
        replay = store._replay(conn, project_id, request_id, "master.assign", body)
        if replay is not None:
            return replay
        row = conn.execute(
            "SELECT epoch FROM master_lease WHERE project_id = ?", (project_id,)
        ).fetchone()
        existing = row["epoch"] if row is not None else 0
        if lease_epoch != existing:
            raise MasterLeaseExpired(lease_epoch, existing)

        ticket = conn.execute(
            "SELECT * FROM tickets WHERE project_id = ? AND id = ?",
            (project_id, ticket_id),
        ).fetchone()
        if ticket is None:
            raise NotFound("No such ticket in this project.",
                           {"ticket_id": ticket_id})
        if ticket["version"] != expected_version:
            raise TicketVersionConflict(ticket_id, expected_version,
                                        ticket["version"])
        agent = conn.execute(
            "SELECT id FROM agents WHERE id = ? AND project_id = ?",
            (agent_id, project_id),
        ).fetchone()
        if agent is None:
            raise NotFound("No such agent in this project.", {"agent_id": agent_id})

        aid = ids.assignment_id()
        conn.execute(
            "INSERT INTO assignments (id, project_id, ticket_id, agent_id, state,"
            " reason, created_at, expires_at, lease_epoch, version)"
            " VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, 1)",
            (aid, project_id, ticket_id, agent_id, reason, ids.now(),
             in_seconds(ttl_seconds), lease_epoch),
        )
        conn.execute(
            "INSERT INTO master_decisions (project_id, ticket_id, decision, reason,"
            " agent_id, decided_at) VALUES (?, ?, 'assigned', ?, ?, ?)",
            (project_id, ticket_id, reason, agent_id, ids.now()),
        )
        store._audit(conn, project_id, actor, "assignment.create",
                     subject_type="assignment", subject_id=aid,
                     request_id=request_id,
                     summary="{} reserved for {}".format(ticket_id, agent_id))
        result = _assignment(conn, aid)
        store._remember(conn, project_id, request_id, "master.assign", body, result)
    return result


def expire_assignments(store, project_id):
    """Mark elapsed reservations expired.

    Called on the read paths rather than by a timer: a reservation that has run
    out must not still render as "Queued -- waiting for agent", and V1 has no
    background scheduler of its own (that is T-182's sweep).

    No audit row is written. Expiry is the clock, not an actor -- attributing it
    to whoever happened to load the page would put a fiction in an append-only
    trail, and writing a row on every GET would make reads generate stream
    events. The audited expiry belongs in T-182's sweep, which has a real actor.

    The read before the write keeps a plain GET off the write lock entirely on
    the overwhelmingly common path where nothing has elapsed.
    """
    stale = store.conn.execute(
        "SELECT 1 FROM assignments WHERE project_id = ? AND state = 'queued'"
        " AND expires_at <= ? LIMIT 1",
        (project_id, ids.now()),
    ).fetchone()
    if stale is None:
        return
    with write_txn(store.conn) as conn:
        conn.execute(
            "UPDATE assignments SET state = 'expired', version = version + 1"
            " WHERE project_id = ? AND state = 'queued' AND expires_at <= ?",
            (project_id, ids.now()),
        )


def queued_assignment(store, project_id, ticket_id):
    return store.conn.execute(
        "SELECT * FROM assignments WHERE project_id = ? AND ticket_id = ?"
        " ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (project_id, ticket_id),
    ).fetchone()


def get_assignment(store, project_id, assignment_id):
    return store.conn.execute(
        "SELECT * FROM assignments WHERE project_id = ? AND id = ?",
        (project_id, assignment_id),
    ).fetchone()


def queue(store, project_id):
    return store.conn.execute(
        "SELECT * FROM assignments WHERE project_id = ? AND state = 'queued'"
        " ORDER BY created_at",
        (project_id,),
    ).fetchall()


def decisions(store, project_id, limit=20):
    rows = store.conn.execute(
        "SELECT * FROM master_decisions WHERE project_id = ?"
        " ORDER BY seq DESC LIMIT ?",
        (project_id, limit),
    ).fetchall()
    return [
        {
            "ticket_id": r["ticket_id"],
            "decision": r["decision"],
            "reason": r["reason"],
            "agent_id": r["agent_id"],
            "decided_at": r["decided_at"],
        }
        for r in rows
    ]


def _serialize(conn, project_id):
    from .views import serialize_master_lease

    row = conn.execute(
        "SELECT * FROM master_lease WHERE project_id = ?", (project_id,)
    ).fetchone()
    return serialize_master_lease(row)


def _assignment(conn, assignment_id):
    from .views import serialize_assignment

    return serialize_assignment(
        conn.execute("SELECT * FROM assignments WHERE id = ?",
                     (assignment_id,)).fetchone()
    )
