"""The fenced loop: pause, run-once, checkpoint, expiry, and two masters.

The property under test throughout is that a master which has been superseded
cannot change the board, in every way it might try: mid-pass, at the checkpoint,
at expiry, and after a restart.
"""

import pytest

from ticket_board.orchestrator import Sweeper
from ticket_board.server import master as master_ops
from ticket_board.server.auth import in_seconds
from ticket_board.server.errors import MasterLeaseExpired


# ------------------------------------------------------------------ pausing

def test_a_paused_board_routes_nothing_and_says_so(
        store, project, operator, lease, sweeper, make_agent, make_ticket):
    make_agent("backend-1", role="backend")
    make_ticket("work", role="backend")
    master_ops.set_paused(store, project, operator, lease_epoch=lease, paused=True)

    result = sweeper.run_once(force=True)
    assert result.status == "paused"
    assert result.decisions == []
    assert master_ops.queue(store, project) == []


def test_resuming_routes_again(
        store, project, operator, lease, sweeper, make_agent, make_ticket):
    make_agent("backend-1", role="backend")
    make_ticket("work", role="backend")
    master_ops.set_paused(store, project, operator, lease_epoch=lease, paused=True)
    assert sweeper.run_once(force=True).status == "paused"

    master_ops.set_paused(store, project, operator, lease_epoch=lease, paused=False)
    assert sweeper.run_once(force=True).status == "swept"
    assert len(master_ops.queue(store, project)) == 1


def test_pause_outranks_run_once(
        store, project, operator, lease, sweeper, make_agent, make_ticket):
    """`force` is a schedule override, not an authority override."""
    make_agent("backend-1", role="backend")
    make_ticket("work", role="backend")
    master_ops.set_paused(store, project, operator, lease_epoch=lease, paused=True)

    assert sweeper.run_once(force=True).status == "paused"


# --------------------------------------------------------------- scheduling

def test_a_sweep_that_is_not_due_does_nothing_until_forced(
        store, project, lease, sweeper, make_agent, make_ticket):
    make_agent("backend-1", role="backend")
    make_ticket("work", role="backend")
    store.conn.execute(
        "UPDATE master_lease SET next_sweep_at = ? WHERE project_id = ?",
        (in_seconds(3600), project))
    store.conn.commit()

    assert sweeper.run_once().status == "not_due"
    assert master_ops.queue(store, project) == []
    assert sweeper.run_once(force=True).status == "swept"


def test_a_sweep_moves_the_checkpoint_forward(
        store, project, lease, sweeper, make_agent, make_ticket):
    make_agent("backend-1", role="backend")
    make_ticket("work", role="backend")
    before = master_ops.lease_row(store, project)["last_sweep_at"]

    sweeper.run_once(force=True)
    after = master_ops.lease_row(store, project)
    assert before is None and after["last_sweep_at"] is not None
    assert after["next_sweep_at"] > after["last_sweep_at"], \
        "the next sweep must be scheduled, or a restart sweeps forever"


def test_a_restarted_master_resumes_from_the_checkpoint_instead_of_storming(
        store, project, operator, lease, sweeper, make_agent, make_ticket):
    """Crash-recovery must not turn into a routing storm."""
    make_agent("backend-1", role="backend", max_active=5)
    make_ticket("work", role="backend")
    sweeper.run_once(force=True)

    restarted = Sweeper(store, project, operator, lease)
    assert restarted.run_once().status == "not_due"


# ------------------------------------------------------------------ expiry

def test_an_elapsed_reservation_expires_with_an_audit_row_and_a_reason(
        store, project, lease, sweeper, make_agent, make_ticket):
    """The audited expiry `server/master.py` deliberately left to this ticket."""
    agent = make_agent("backend-1", role="backend")
    ticket = make_ticket("work", role="backend")
    sweeper.run_once(force=True)
    store.conn.execute(
        "UPDATE assignments SET expires_at = ? WHERE project_id = ?",
        (in_seconds(-1), project))
    store.conn.commit()

    result = sweeper.run_once(force=True)
    assert result.expired == 1
    assert master_ops.queue(store, project) == [] or \
        master_ops.queue(store, project)[0]["ticket_id"] == ticket

    audit = store.conn.execute(
        "SELECT * FROM audit_events WHERE project_id = ? AND action ="
        " 'assignment.expire'", (project,)).fetchall()
    assert len(audit) == 1, "expiry under a real lease is attributable"
    assert agent in audit[0]["summary"]

    logged = [d for d in master_ops.decisions(store, project)
              if d["reason"] == "reservation expired unclaimed"]
    assert len(logged) == 1


# ------------------------------------------------------- decisions are logged

def test_every_ticket_that_did_not_move_leaves_a_reason(
        store, project, lease, sweeper, make_agent, make_ticket):
    make_agent("console-1", role="console")
    wrong_role = make_ticket("backend work", role="backend")
    blocker = make_ticket("first", role="console")
    waiting = make_ticket("second", role="console", dependencies=[blocker])

    sweeper.run_once(force=True)
    logged = {d["ticket_id"]: d for d in master_ops.decisions(store, project)}

    assert logged[wrong_role]["decision"] == "no_eligible_agent"
    assert logged[waiting]["decision"] == "skipped"
    assert "waiting on %s" % blocker == logged[waiting]["reason"]
    assert logged[blocker]["decision"] == "assigned"


# ------------------------------------------------------- two competing masters

def test_a_superseded_master_cannot_route(
        store, project, operator, lease, sweeper, make_agent, make_ticket):
    make_agent("backend-1", role="backend")
    make_ticket("work", role="backend")

    rival = {"type": "master", "id": "mem_rival", "display_name": "Rival",
             "session_id": "ses_rival"}
    master_ops.take_lease(store, project, rival, expected_epoch=lease)

    result = sweeper.run_once(force=True)
    assert result.status == "superseded"
    assert master_ops.queue(store, project) == [], \
        "the old master must not have written a reservation"


def test_the_new_master_routes_normally_after_taking_over(
        store, project, operator, lease, sweeper, make_agent, make_ticket):
    make_agent("backend-1", role="backend")
    ticket = make_ticket("work", role="backend")
    rival = {"type": "master", "id": "mem_rival", "display_name": "Rival",
             "session_id": "ses_rival"}
    master_ops.take_lease(store, project, rival, expected_epoch=lease)
    store.conn.execute(
        "UPDATE master_lease SET next_sweep_at = ? WHERE project_id = ?",
        (in_seconds(-1), project))
    store.conn.commit()

    winner = Sweeper(store, project, rival,
                     master_ops.current_epoch(store, project))
    result = winner.run_once(force=True)
    assert result.status == "swept"
    assert [d.ticket_id for d in result.assigned] == [ticket]


def test_two_masters_racing_the_same_epoch_cannot_both_win(
        store, project, operator, lease):
    """The CAS is the fence; only one of two identical takeovers succeeds."""
    rival_a = {"type": "master", "id": "mem_a", "display_name": "A",
               "session_id": "ses_a"}
    rival_b = {"type": "master", "id": "mem_b", "display_name": "B",
               "session_id": "ses_b"}

    master_ops.take_lease(store, project, rival_a, expected_epoch=lease)
    with pytest.raises(Exception) as caught:
        master_ops.take_lease(store, project, rival_b, expected_epoch=lease)
    assert "conflict" in type(caught.value).__name__.lower()
    holder = master_ops.lease_row(store, project)["holder"]
    assert "mem_a" in holder and "mem_b" not in holder


def test_a_superseded_master_cannot_move_the_checkpoint(
        store, project, operator, lease, sweeper):
    rival = {"type": "master", "id": "mem_rival", "display_name": "Rival",
             "session_id": "ses_rival"}
    master_ops.take_lease(store, project, rival, expected_epoch=lease)

    with pytest.raises(MasterLeaseExpired):
        sweeper.checkpoint()


def test_a_superseded_master_cannot_expire_reservations(
        store, project, operator, lease, sweeper, make_agent, make_ticket):
    make_agent("backend-1", role="backend")
    make_ticket("work", role="backend")
    sweeper.run_once(force=True)
    store.conn.execute(
        "UPDATE assignments SET expires_at = ? WHERE project_id = ?",
        (in_seconds(-1), project))
    store.conn.commit()

    rival = {"type": "master", "id": "mem_rival", "display_name": "Rival",
             "session_id": "ses_rival"}
    master_ops.take_lease(store, project, rival, expected_epoch=lease)

    with pytest.raises(MasterLeaseExpired):
        sweeper.expire_reservations()


def test_a_superseded_master_cannot_write_to_the_decision_log(
        store, project, operator, lease, sweeper, make_agent, make_ticket):
    """Found by a surviving mutation, not by design.

    Every other fenced write had a direct test; the decision log did not, so
    removing its epoch check broke nothing. A master superseded midway through a
    pass would have gone on appending reasons to the log the new master owns --
    quiet, plausible and wrong, which is the worst kind of row to find in an
    append-only trail.
    """
    ticket = make_ticket("work", role="backend")
    rival = {"type": "master", "id": "mem_rival", "display_name": "Rival",
             "session_id": "ses_rival"}
    master_ops.take_lease(store, project, rival, expected_epoch=lease)

    with pytest.raises(MasterLeaseExpired):
        sweeper._log(ticket, "skipped", "written by a master that lost the lease",
                     None)
    assert master_ops.decisions(store, project) == []


def test_a_sweeper_never_promotes_itself_back_to_current(
        store, project, operator, lease, sweeper, make_agent, make_ticket):
    """Losing the lease is permanent for that object.

    A Sweeper that re-read the epoch would quietly become authoritative again
    the moment the rival's lease lapsed and someone else took over -- which is
    the fence failing open.
    """
    make_agent("backend-1", role="backend")
    make_ticket("work", role="backend")
    rival = {"type": "master", "id": "mem_rival", "display_name": "Rival",
             "session_id": "ses_rival"}
    master_ops.take_lease(store, project, rival, expected_epoch=lease)
    assert sweeper.run_once(force=True).status == "superseded"

    master_ops.take_lease(store, project, operator,
                          expected_epoch=master_ops.current_epoch(store, project))
    assert sweeper.run_once(force=True).status == "superseded", \
        "the old object stays dead even when its holder holds the lease again"


# ------------------------------------------------- no merges, no takeovers

def test_the_sweep_never_changes_a_ticket_state_or_owner(
        store, project, lease, sweeper, make_agent, make_ticket):
    """A reservation is an offer. The agent still has to claim it."""
    make_agent("backend-1", role="backend")
    ticket = make_ticket("work", role="backend")
    before = dict(store.conn.execute(
        "SELECT state, owner, version FROM tickets WHERE project_id = ? AND id = ?",
        (project, ticket)).fetchone())

    sweeper.run_once(force=True)
    after = dict(store.conn.execute(
        "SELECT state, owner, version FROM tickets WHERE project_id = ? AND id = ?",
        (project, ticket)).fetchone())
    assert before == after
    assert len(master_ops.queue(store, project)) == 1
