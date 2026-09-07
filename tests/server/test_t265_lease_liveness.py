"""T-265: an expired session lease must stop unsafe agent writes, not just claim.

`_agent_principal` only checks `agent_tokens.revoked_at`; a lease that merely
*expired* on the clock left the token itself fully valid, and only
`claim_ticket` ever asked the lease's own liveness. Every other agent-writable
route -- POST /tickets, POST /tickets/{id}/updates, POST
/tickets/{id}/reviews, POST /tickets/{id}/blocked -- sailed through on a dead
lease. These tests exercise the real clock (`UPDATE session_leases SET
expires_at = ...`), never a monkeypatched check.
"""

import uuid

from api_client import rid
from ticket_board.server.auth import in_seconds


def _expire(server, session_id):
    server.store.conn.execute(
        "UPDATE session_leases SET expires_at = ? WHERE session_id = ?",
        (in_seconds(-60), session_id),
    )


def test_an_expired_lease_still_reads(server, enrolled, ticket):
    """Reads stay allowed: an agent that can no longer act should stay observable."""
    _expire(server, enrolled["session_id"])
    assert enrolled["client"].get("/overview").status == 200
    assert enrolled["client"].get("/tickets").status == 200
    assert enrolled["client"].get("/tickets/{}".format(ticket["id"])).status == 200


def test_an_expired_lease_cannot_create_a_ticket(server, enrolled):
    _expire(server, enrolled["session_id"])
    response = enrolled["client"].post("/tickets", {
        "request_id": rid(), "title": "Should not land", "outcome": "o",
        "acceptance": [{"text": "a"}],
    })
    assert response.status == 409
    assert response.json()["error"]["code"] == "session_lease_expired"


def test_an_expired_lease_cannot_post_a_ticket_update(server, enrolled, ticket):
    claimed = enrolled["client"].post(
        "/tickets/{}/claim".format(ticket["id"]),
        {"request_id": rid(), "expected_version": ticket["version"],
         "session_id": enrolled["session_id"]},
    )
    assert claimed.status == 200
    _expire(server, enrolled["session_id"])
    response = enrolled["client"].post(
        "/tickets/{}/updates".format(ticket["id"]),
        {"request_id": rid(), "body": "still working", "next_step": "finish it"},
    )
    assert response.status == 409
    assert response.json()["error"]["code"] == "session_lease_expired"


def test_an_expired_lease_cannot_request_review(server, enrolled, ticket):
    claimed = enrolled["client"].post(
        "/tickets/{}/claim".format(ticket["id"]),
        {"request_id": rid(), "expected_version": ticket["version"],
         "session_id": enrolled["session_id"]},
    )
    assert claimed.status == 200
    _expire(server, enrolled["session_id"])
    response = enrolled["client"].post(
        "/tickets/{}/reviews".format(ticket["id"]),
        {"request_id": rid(), "expected_version": claimed.json()["version"],
         "evidence": {"repository": "demo-org/demo-repo", "branch": "feature/x",
                      "sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"},
         "notes": "done"},
    )
    assert response.status == 409
    assert response.json()["error"]["code"] == "session_lease_expired"


def test_an_expired_lease_cannot_mark_a_ticket_blocked(server, enrolled, ticket):
    _expire(server, enrolled["session_id"])
    response = enrolled["client"].post(
        "/tickets/{}/blocked".format(ticket["id"]),
        {"request_id": rid(), "expected_version": ticket["version"],
         "blocked": True, "reason": "waiting"},
    )
    assert response.status == 409
    assert response.json()["error"]["code"] == "session_lease_expired"


def test_a_revoked_lease_is_401_before_the_lease_check_even_runs(
        server, operator, enrolled):
    """Revocation kills the token itself, so this never reaches T-265's check."""
    agents = operator.get("/agents").json()["items"]
    version = next(a for a in agents if a["id"] == enrolled["agent_id"])["version"]
    revoked = operator.delete(
        "/agents/{}/session-lease".format(enrolled["agent_id"]),
        {"request_id": rid(), "expected_version": version, "note": "gone"})
    assert revoked.status == 200
    response = enrolled["client"].post("/tickets", {
        "request_id": rid(), "title": "x", "outcome": "o",
        "acceptance": [{"text": "a"}],
    })
    assert response.status == 401


def test_hook_events_are_the_named_exception_and_still_heal_a_lapsed_lease(
        server, enrolled):
    """POST /hook-events must stay reachable on an expired lease -- it is the
    only way a lapsed (not revoked) agent ever gets its lease current again.
    """
    _expire(server, enrolled["session_id"])
    event = {"event_id": "hev_" + uuid.uuid4().hex,
             "agent_id": enrolled["agent_id"],
             "session_id": enrolled["session_id"], "kind": "session_start",
             "occurred_at": "2026-09-06T14:32:00Z"}
    response = enrolled["client"].post("/hook-events",
                                       {"request_id": rid(), "event": event})
    assert response.status == 200
    lease = server.store.get_session(enrolled["session_id"])
    assert lease["expires_at"] > in_seconds(0)
