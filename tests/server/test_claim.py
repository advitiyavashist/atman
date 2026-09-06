"""Claim: the concurrency-critical route, and the checks around it."""

import uuid

import pytest

from api_client import Client, rid
from ticket_board.server.auth import in_seconds


def claim(client, ticket_id, version, session_id, **extra):
    body = {"request_id": rid(), "expected_version": version,
            "session_id": session_id}
    body.update(extra)
    return client.post("/tickets/{}/claim".format(ticket_id), body)


def test_a_claim_takes_ownership_and_bumps_the_version(enrolled, ticket):
    response = claim(enrolled["client"], ticket["id"], ticket["version"],
                     enrolled["session_id"])
    assert response.status == 200
    body = response.json()
    assert body["state"] == "claimed"
    assert body["owner"] == enrolled["agent_id"]
    assert body["owner_session"] == enrolled["session_id"]
    assert body["version"] == ticket["version"] + 1


def test_a_second_agent_loses_with_ticket_already_claimed(server, operator,
                                                          project, enrolled,
                                                          ticket):
    second = _enroll(server, operator, project, "backend-2")
    first = claim(enrolled["client"], ticket["id"], ticket["version"],
                  enrolled["session_id"])
    assert first.status == 200
    loser = claim(second["client"], ticket["id"], first.json()["version"],
                  second["session_id"])
    assert loser.status == 409
    assert loser.json()["error"]["code"] == "ticket_already_claimed"
    assert loser.json()["error"]["details"]["owner"] == enrolled["agent_id"]


def test_a_stale_version_carries_both_versions_so_one_reread_is_enough(
        enrolled, ticket):
    claim(enrolled["client"], ticket["id"], ticket["version"],
          enrolled["session_id"])
    stale = claim(enrolled["client"], ticket["id"], ticket["version"],
                  enrolled["session_id"])
    assert stale.status == 409
    details = stale.json()["error"]["details"]
    assert details["expected_version"] == ticket["version"]
    assert details["actual_version"] == ticket["version"] + 1


def test_capacity_is_enforced(operator, enrolled, ticket):
    second = operator.post("/tickets", {
        "request_id": rid(), "title": "Second", "outcome": "o",
        "acceptance": [{"text": "a"}]}).json()
    assert claim(enrolled["client"], ticket["id"], ticket["version"],
                 enrolled["session_id"]).status == 200
    response = claim(enrolled["client"], second["id"], second["version"],
                     enrolled["session_id"])
    assert response.status == 409
    payload = response.json()["error"]
    assert payload["code"] == "capacity_exhausted"
    assert payload["details"]["max_active_tickets"] == 1


def test_an_expired_session_lease_cannot_claim(server, enrolled, ticket):
    """The release bar: an agent whose adapter is failing cannot claim offline.

    The store's own check only asks whether a lease was *revoked*, which is the
    right question for a superseded update and the wrong one here -- a lapsed
    adapter revokes nothing, it just stops arriving.
    """
    server.store.conn.execute(
        "UPDATE session_leases SET expires_at = ? WHERE session_id = ?",
        (in_seconds(-60), enrolled["session_id"]),
    )
    response = claim(enrolled["client"], ticket["id"], ticket["version"],
                     enrolled["session_id"])
    assert response.status == 409
    assert response.json()["error"]["code"] == "session_lease_expired"
    assert response.json()["error"]["details"]["reason"] == "lease expired"


def test_a_revoked_lease_cannot_claim(server, operator, enrolled, ticket):
    agents = operator.get("/agents").json()["items"]
    version = next(a for a in agents if a["id"] == enrolled["agent_id"])["version"]
    revoked = operator.delete(
        "/agents/{}/session-lease".format(enrolled["agent_id"]),
        {"request_id": rid(), "expected_version": version,
         "note": "Unreachable for 12m; work preserved on the branch."})
    assert revoked.status == 200
    # The token went with the lease, so the agent cannot even authenticate.
    response = claim(enrolled["client"], ticket["id"], ticket["version"],
                     enrolled["session_id"])
    assert response.status == 401


def test_a_session_belonging_to_another_agent_cannot_be_borrowed(
        server, operator, project, enrolled, ticket):
    second = _enroll(server, operator, project, "backend-2")
    response = claim(enrolled["client"], ticket["id"], ticket["version"],
                     second["session_id"])
    assert response.status == 409
    assert response.json()["error"]["details"]["reason"] == \
        "no such session for this agent"


def test_an_unmet_dependency_is_422_and_claims_nothing(operator, enrolled):
    blocker = operator.post("/tickets", {
        "request_id": rid(), "title": "Blocker", "outcome": "o",
        "acceptance": [{"text": "a"}]}).json()
    dependent = operator.post("/tickets", {
        "request_id": rid(), "title": "Dependent", "outcome": "o",
        "acceptance": [{"text": "a"}], "dependencies": [blocker["id"]]}).json()
    assert dependent["dependency_blocked"] is True
    response = claim(enrolled["client"], dependent["id"], dependent["version"],
                     enrolled["session_id"])
    assert response.status == 422
    assert response.json()["error"]["code"] == "dependency_unmet"
    after = operator.get("/tickets/" + dependent["id"]).json()["ticket"]
    assert after["state"] == "open" and after["version"] == dependent["version"]


def test_dependency_blocked_is_a_flag_not_a_state(operator, enrolled):
    """Filtering on it as a state is a 400; filtering on the flag works."""
    assert operator.get("/tickets",
                        query="state=dependency_blocked").status == 400
    blocker = operator.post("/tickets", {
        "request_id": rid(), "title": "Blocker", "outcome": "o",
        "acceptance": [{"text": "a"}]}).json()
    operator.post("/tickets", {
        "request_id": rid(), "title": "Dependent", "outcome": "o",
        "acceptance": [{"text": "a"}], "dependencies": [blocker["id"]]})
    listing = operator.get("/tickets", query="dependency_blocked=true").json()
    assert [t["title"] for t in listing["items"]] == ["Dependent"]
    assert all(t["state"] == "open" for t in listing["items"])


# ---------------------------------------------------------------- reservations

def test_claiming_against_a_reservation_marks_it_claimed(operator, enrolled,
                                                          ticket):
    lease = operator.post("/master/lease", {"request_id": rid(),
                                            "expected_epoch": 0}).json()
    assignment = operator.post("/assignments", {
        "request_id": rid(), "ticket_id": ticket["id"],
        "agent_id": enrolled["agent_id"], "reason": "Only free backend agent.",
        "lease_epoch": lease["epoch"], "expected_version": ticket["version"],
    }).json()
    assert assignment["state"] == "queued"
    response = claim(enrolled["client"], ticket["id"], ticket["version"],
                     enrolled["session_id"], assignment_id=assignment["id"])
    assert response.status == 200
    detail = operator.get("/tickets/" + ticket["id"]).json()
    assert detail["assignment"]["state"] == "claimed"


def test_an_expired_reservation_cannot_be_claimed(server, operator, enrolled,
                                                   ticket):
    lease = operator.post("/master/lease", {"request_id": rid(),
                                            "expected_epoch": 0}).json()
    assignment = operator.post("/assignments", {
        "request_id": rid(), "ticket_id": ticket["id"],
        "agent_id": enrolled["agent_id"], "reason": "r",
        "lease_epoch": lease["epoch"], "expected_version": ticket["version"],
    }).json()
    server.store.conn.execute(
        "UPDATE assignments SET expires_at = ? WHERE id = ?",
        (in_seconds(-60), assignment["id"]),
    )
    response = claim(enrolled["client"], ticket["id"], ticket["version"],
                     enrolled["session_id"], assignment_id=assignment["id"])
    assert response.status == 409
    assert response.json()["error"]["code"] == "assignment_expired"


def test_another_agents_reservation_is_not_visible_as_a_claim_route(
        server, operator, project, enrolled, ticket):
    second = _enroll(server, operator, project, "backend-2")
    lease = operator.post("/master/lease", {"request_id": rid(),
                                            "expected_epoch": 0}).json()
    assignment = operator.post("/assignments", {
        "request_id": rid(), "ticket_id": ticket["id"],
        "agent_id": second["agent_id"], "reason": "r",
        "lease_epoch": lease["epoch"], "expected_version": ticket["version"],
    }).json()
    response = claim(enrolled["client"], ticket["id"], ticket["version"],
                     enrolled["session_id"], assignment_id=assignment["id"])
    assert response.status == 404


def test_a_queued_reservation_never_reads_as_working(operator, enrolled, ticket):
    """Copy that is contract, not styling: a reservation is not work in progress."""
    lease = operator.post("/master/lease", {"request_id": rid(),
                                            "expected_epoch": 0}).json()
    operator.post("/assignments", {
        "request_id": rid(), "ticket_id": ticket["id"],
        "agent_id": enrolled["agent_id"], "reason": "r",
        "lease_epoch": lease["epoch"], "expected_version": ticket["version"]})
    detail = operator.get("/tickets/" + ticket["id"]).json()
    assert detail["assignment"]["state"] == "queued"
    assert detail["ticket"]["state"] == "open"
    assert detail["ticket"]["owner"] is None


def _enroll(server, operator, project, name):
    created = operator.post("/enrollments", {
        "request_id": rid(), "agent_name": name, "role": "backend",
        "connection_mode": "managed"})
    session_id = "ses_" + uuid.uuid4().hex[:8]
    exchanged = Client(server, project_id=project["id"]).post("/sessions", {
        "request_id": rid(), "code": created.json()["code"],
        "session_id": session_id})
    payload = exchanged.json()
    return {"agent_id": payload["agent"]["id"], "session_id": session_id,
            "token": payload["token"],
            "client": Client(server, project_id=project["id"],
                             token=payload["token"], origin=None)}


def test_a_revoked_lease_cannot_claim(server, operator, project, enrolled,
                                      ticket):
    """Revocation is the operator's stop button; a claim is what it must stop.

    Distinct from the expired-lease case: expiry is the adapter going quiet,
    revocation is an operator deciding this session is done. Both have to
    refuse a claim, and the store checks neither -- `_session_is_current` asks
    only about revocation, and only to decide whether an update is superseded.
    """
    agents = operator.get("/agents").json()["items"]
    version = next(a for a in agents
                   if a["id"] == enrolled["agent_id"])["version"]
    revoked = operator.delete(
        "/agents/{}/session-lease".format(enrolled["agent_id"]),
        {"request_id": rid(), "expected_version": version,
         "note": "Adapter unreachable."})
    assert revoked.status == 200

    response = claim(enrolled["client"], ticket["id"], ticket["version"],
                     enrolled["session_id"])
    # The token is revoked with the lease, so the credential no longer
    # authenticates at all -- the claim is refused before it is even scoped.
    assert response.status == 401
    assert server.store.get_ticket(project["id"], ticket["id"])["owner"] is None


def test_a_revoked_lease_refuses_the_claim_even_if_the_token_survives(
        server, operator, project, enrolled, ticket):
    """The lease check on its own, with authentication held constant.

    The route-level guard must not be reachable only through token revocation:
    revoke the lease directly, leave the token alone, and the claim still has
    to fail as session_lease_expired.
    """
    server.store.conn.execute(
        "UPDATE session_leases SET revoked_at = ? WHERE session_id = ?",
        (in_seconds(-1), enrolled["session_id"]))
    server.store.conn.commit()

    response = claim(enrolled["client"], ticket["id"], ticket["version"],
                     enrolled["session_id"])
    assert response.status == 409
    assert response.json()["error"]["code"] == "session_lease_expired"
    assert response.json()["error"]["details"]["reason"] == "revoked"
    assert server.store.get_ticket(project["id"], ticket["id"])["owner"] is None


@pytest.mark.parametrize("bad", ["", "sess_abcdefgh", "ses_AB", "ses_" + "z" * 40,
                                 "../../etc/passwd"])
def test_a_malformed_session_id_is_refused_before_the_claim(enrolled, ticket, bad):
    """A shape check, so a junk id cannot reach the lease lookup as a wildcard."""
    response = claim(enrolled["client"], ticket["id"], ticket["version"], bad)
    assert response.status == 400
    assert response.json()["error"]["code"] == "malformed_request"
