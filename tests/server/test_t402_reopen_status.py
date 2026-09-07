"""T-402: reopenTicket must not answer an undeclared 422.

openapi.yaml reopenTicket declares {200,400,401,403,404,409} only.
store.transition raises InvalidStateTransition (status 422) for DONE->open and
open->open. T-288's rule is that where code and the frozen contract disagree,
the CODE is wrong — but mapping that to 409 vs amending the frozen yaml with
422 is a planner call (asked of cursor 2026-09-08). These tests stay xfail
until that ruling; they are already red against 422.

Replay of an identical request_id after a successful reopen must stay 200
(_replay runs before the state check).
"""
import pytest

from api_client import rid

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"

DECLARED = {200, 400, 401, 403, 404, 409}

_XFAIL_RULING = pytest.mark.xfail(
    reason="T-402 waiting planner: map InvalidStateTransition to 409, or add 422 to frozen reopenTicket",
    strict=True,
)


def _reopen(operator, ticket_id, version, request_id=None, reason="recover"):
    return operator.post("/tickets/%s/reopen" % ticket_id, {
        "request_id": request_id or rid(),
        "expected_version": version,
        "reason": reason,
    })


@_XFAIL_RULING
def test_reopen_of_an_already_open_ticket_is_a_declared_status(operator, ticket):
    """Live blast: a routing loop reopens a ticket that is already open."""
    assert ticket["state"] == "open"
    response = _reopen(operator, ticket["id"], ticket["version"])
    assert response.status != 422, response.json()
    assert response.status in DECLARED, response.json()


def _done_ticket(operator, enrolled, ticket):
    claimed = enrolled["client"].post("/tickets/%s/claim" % ticket["id"], {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"],
    })
    assert claimed.status == 200, claimed.json()
    review = enrolled["client"].post("/tickets/%s/reviews" % ticket["id"], {
        "request_id": rid(), "expected_version": claimed.json()["version"],
        "evidence": {"repository": "acme/repo", "branch": "b", "sha": SHA},
        "notes": "done for T-402",
    })
    assert review.status == 201, review.json()
    version = operator.get("/tickets/" + ticket["id"]).json()["ticket"]["version"]
    decided = operator.post(
        "/tickets/%s/reviews/%s/decision" % (ticket["id"], review.json()["id"]),
        {"request_id": rid(), "expected_version": version,
         "decision": "accept", "evidence_sha": SHA},
    )
    assert decided.status == 200, decided.json()
    done = operator.get("/tickets/" + ticket["id"]).json()["ticket"]
    assert done["state"] == "done", done
    return done


@_XFAIL_RULING
def test_reopen_of_a_done_ticket_is_a_declared_status(operator, enrolled, ticket):
    done = _done_ticket(operator, enrolled, ticket)
    response = _reopen(operator, done["id"], done["version"])
    assert response.status != 422, response.json()
    assert response.status in DECLARED, response.json()


def test_reopen_replay_of_the_same_request_id_stays_200(operator, enrolled, ticket):
    """_replay must still win so a lost-response retry is not a conflict."""
    claimed = enrolled["client"].post("/tickets/%s/claim" % ticket["id"], {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"],
    })
    assert claimed.status == 200, claimed.json()
    body_rid = rid()
    first = _reopen(operator, ticket["id"], claimed.json()["version"],
                    request_id=body_rid, reason="silent agent")
    assert first.status == 200, first.json()
    replay = _reopen(operator, ticket["id"], claimed.json()["version"],
                     request_id=body_rid, reason="silent agent")
    assert replay.status == 200, replay.json()
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["state"] == "open"
