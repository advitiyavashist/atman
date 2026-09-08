"""T-402: reopenTicket maps InvalidStateTransition to declared 409.

Planner ruling (claude-fable): frozen reopenTicket has no 422; the CODE is
wrong. HTTP 409, existing ErrorCode invalid_state_transition, details {from, to}
unchanged. No contract edit. Replay of an identical request_id after a
successful reopen stays 200 (_replay runs before the state check).
"""
from api_client import rid

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


def _reopen(operator, ticket_id, version, request_id=None, reason="recover"):
    return operator.post("/tickets/%s/reopen" % ticket_id, {
        "request_id": request_id or rid(),
        "expected_version": version,
        "reason": reason,
    })


def _assert_conflict_transition(response, from_state, to_state="open"):
    assert response.status == 409, response.json()
    err = response.json()["error"]
    assert err["status"] == 409
    assert err["code"] == "invalid_state_transition"
    assert err["details"]["from"] == from_state
    assert err["details"]["to"] == to_state


def test_reopen_of_an_already_open_ticket_is_409(operator, ticket):
    """Live blast: a routing loop reopens a ticket that is already open."""
    assert ticket["state"] == "open"
    response = _reopen(operator, ticket["id"], ticket["version"])
    _assert_conflict_transition(response, "open")


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


def test_reopen_of_a_done_ticket_is_409(operator, enrolled, ticket):
    done = _done_ticket(operator, enrolled, ticket)
    response = _reopen(operator, done["id"], done["version"])
    _assert_conflict_transition(response, "done")


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


def test_other_invalid_transitions_still_answer_422(operator, enrolled, ticket):
    """The 409 remap is reopenTicket-only; decideReview still uses 422."""
    claimed = enrolled["client"].post("/tickets/%s/claim" % ticket["id"], {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"],
    })
    assert claimed.status == 200, claimed.json()
    review = enrolled["client"].post("/tickets/%s/reviews" % ticket["id"], {
        "request_id": rid(), "expected_version": claimed.json()["version"],
        "evidence": {"repository": "acme/repo", "branch": "b", "sha": SHA},
        "notes": "first",
    })
    assert review.status == 201, review.json()
    version = operator.get("/tickets/" + ticket["id"]).json()["ticket"]["version"]
    first = operator.post(
        "/tickets/%s/reviews/%s/decision" % (ticket["id"], review.json()["id"]),
        {"request_id": rid(), "expected_version": version,
         "decision": "accept", "evidence_sha": SHA},
    )
    assert first.status == 200, first.json()
    version = operator.get("/tickets/" + ticket["id"]).json()["ticket"]["version"]
    again = operator.post(
        "/tickets/%s/reviews/%s/decision" % (ticket["id"], review.json()["id"]),
        {"request_id": rid(), "expected_version": version,
         "decision": "accept", "evidence_sha": SHA},
    )
    assert again.status == 422, again.json()
    assert again.json()["error"]["code"] == "invalid_state_transition"
    assert again.json()["error"]["status"] == 422
