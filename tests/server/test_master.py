"""Master lease CAS, epoch fencing and the panel."""

import pytest

from api_client import Client, rid
from ticket_board.server.auth import in_seconds


def take(client, expected_epoch=0, **extra):
    body = {"request_id": rid(), "expected_epoch": expected_epoch}
    body.update(extra)
    return client.post("/master/lease", body)


def test_an_unheld_board_answers_with_no_holder(operator):
    """The panel renders "no master" rather than erroring on a fresh board."""
    panel = operator.get("/master").json()
    assert panel["lease"]["holder"] is None
    assert panel["lease"]["epoch"] == 0
    assert panel["queue"] == [] and panel["decisions"] == []


def test_taking_the_lease_increments_the_epoch(operator):
    lease = take(operator).json()
    assert lease["epoch"] == 1
    assert lease["holder"]["type"] == "master"
    assert take(operator, expected_epoch=1).json()["epoch"] == 2


def test_two_masters_racing_the_same_epoch_yield_one_winner(server, project,
                                                             operator):
    second = server.bootstrap_operator(project["id"], display_name="Master 2")
    rival = Client(server, project_id=project["id"],
                   cookie=second["session_token"], csrf=second["csrf_token"])
    assert take(operator, 0).status == 200
    loser = take(rival, 0)
    assert loser.status == 409
    payload = loser.json()["error"]
    assert payload["code"] == "master_lease_conflict"
    assert payload["details"] == {"expected_epoch": 0, "actual_epoch": 1}


def test_a_live_lease_can_still_be_taken_with_the_right_epoch(operator):
    """Epoch is the fence, not expiry.

    Requiring the current lease to have expired would let a live but
    unresponsive master hold the board hostage.
    """
    lease = take(operator).json()
    assert lease["expires_at"] > lease["acquired_at"]
    assert take(operator, expected_epoch=lease["epoch"]).status == 200


def test_an_expired_lease_shows_no_holder_on_the_panel(server, project, operator):
    take(operator)
    server.store.conn.execute(
        "UPDATE master_lease SET expires_at = ? WHERE project_id = ?",
        (in_seconds(-60), project["id"]))
    lease = operator.get("/master").json()["lease"]
    assert lease["holder"] is None
    # The epoch survives: fencing must not reset when a lease lapses.
    assert lease["epoch"] == 1


def test_pause_is_fenced_by_the_epoch(operator):
    lease = take(operator).json()
    stale = operator.post("/master/pause", {
        "request_id": rid(), "lease_epoch": lease["epoch"] - 1, "paused": True})
    assert stale.status == 409
    assert stale.json()["error"]["code"] == "master_lease_expired"
    assert stale.json()["error"]["details"] == {"sent_epoch": 0, "current_epoch": 1}

    ok = operator.post("/master/pause", {
        "request_id": rid(), "lease_epoch": lease["epoch"], "paused": True})
    assert ok.status == 200 and ok.json()["paused"] is True


def test_a_superseded_master_cannot_route(operator, ticket, enrolled):
    lease = take(operator).json()
    take(operator, expected_epoch=lease["epoch"])          # someone took over
    response = operator.post("/assignments", {
        "request_id": rid(), "ticket_id": ticket["id"],
        "agent_id": enrolled["agent_id"], "reason": "r",
        "lease_epoch": lease["epoch"], "expected_version": ticket["version"]})
    assert response.status == 409
    assert response.json()["error"]["code"] == "master_lease_expired"


def test_a_reservation_needs_a_reason(operator, ticket, enrolled):
    """Routing without a recorded reason is not auditable."""
    lease = take(operator).json()
    response = operator.post("/assignments", {
        "request_id": rid(), "ticket_id": ticket["id"],
        "agent_id": enrolled["agent_id"], "lease_epoch": lease["epoch"],
        "expected_version": ticket["version"]})
    assert response.status == 400
    assert response.json()["error"]["details"]["missing_fields"] == ["reason"]


def test_a_reservation_against_a_stale_ticket_version_is_refused(operator,
                                                                  ticket,
                                                                  enrolled):
    """Someone claimed it while the master was deciding."""
    lease = take(operator).json()
    enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]})
    response = operator.post("/assignments", {
        "request_id": rid(), "ticket_id": ticket["id"],
        "agent_id": enrolled["agent_id"], "reason": "r",
        "lease_epoch": lease["epoch"], "expected_version": ticket["version"]})
    assert response.status == 409
    assert response.json()["error"]["code"] == "ticket_version_conflict"


def test_the_panel_shows_the_queue_and_the_reason(operator, ticket, enrolled):
    lease = take(operator).json()
    reason = "Only free agent with the backend capability."
    operator.post("/assignments", {
        "request_id": rid(), "ticket_id": ticket["id"],
        "agent_id": enrolled["agent_id"], "reason": reason,
        "lease_epoch": lease["epoch"], "expected_version": ticket["version"]})
    panel = operator.get("/master").json()
    assert [a["ticket_id"] for a in panel["queue"]] == [ticket["id"]]
    assert panel["queue"][0]["reason"] == reason
    assert panel["decisions"][0] == {
        "ticket_id": ticket["id"], "decision": "assigned", "reason": reason,
        "agent_id": enrolled["agent_id"],
        "decided_at": panel["decisions"][0]["decided_at"],
    }


def test_an_elapsed_reservation_leaves_the_queue(server, operator, ticket,
                                                  enrolled):
    lease = take(operator).json()
    assignment = operator.post("/assignments", {
        "request_id": rid(), "ticket_id": ticket["id"],
        "agent_id": enrolled["agent_id"], "reason": "r",
        "lease_epoch": lease["epoch"], "expected_version": ticket["version"]
    }).json()
    server.store.conn.execute("UPDATE assignments SET expires_at = ? WHERE id = ?",
                              (in_seconds(-60), assignment["id"]))
    assert operator.get("/master").json()["queue"] == []


def test_a_reservation_for_an_unknown_agent_is_404(operator, ticket):
    lease = take(operator).json()
    response = operator.post("/assignments", {
        "request_id": rid(), "ticket_id": ticket["id"],
        "agent_id": "agt_00000000", "reason": "r",
        "lease_epoch": lease["epoch"], "expected_version": ticket["version"]})
    assert response.status == 404


def test_master_mutations_replay_rather_than_double_apply(operator):
    key = rid()
    body = {"request_id": key, "expected_epoch": 0}
    first = operator.post("/master/lease", body)
    second = operator.post("/master/lease", body)
    assert first.status == second.status == 200
    assert first.json() == second.json()
    # A second real takeover would have reached epoch 2.
    assert operator.get("/master").json()["lease"]["epoch"] == 1


def test_pause_replays_rather_than_double_applying(server, operator):
    """T-255: `/master/pause` had a replay guard but no test exercising it."""
    lease = take(operator).json()
    key = rid()
    body = {"request_id": key, "lease_epoch": lease["epoch"], "paused": True}
    first = operator.post("/master/pause", body)
    second = operator.post("/master/pause", body)
    assert first.status == second.status == 200
    assert first.json() == second.json()
    count = server.store.conn.execute(
        "SELECT COUNT(*) FROM audit_events WHERE action = 'master.paused'"
    ).fetchone()[0]
    assert count == 1


def test_assignment_replays_rather_than_double_applying(server, operator, ticket,
                                                         enrolled):
    """T-255: `/assignments` had a replay guard but no test exercising it."""
    lease = take(operator).json()
    key = rid()
    body = {"request_id": key, "ticket_id": ticket["id"],
            "agent_id": enrolled["agent_id"], "reason": "r",
            "lease_epoch": lease["epoch"], "expected_version": ticket["version"]}
    first = operator.post("/assignments", body)
    second = operator.post("/assignments", body)
    assert first.status == second.status == 201
    assert first.json() == second.json()
    count = server.store.conn.execute(
        "SELECT COUNT(*) FROM assignments WHERE ticket_id = ?", (ticket["id"],)
    ).fetchone()[0]
    assert count == 1
