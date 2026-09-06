"""Credentials, scope and CSRF -- the trust boundary of the board.

Each test names the property it defends, because the failure mode for most of
them is silent: a check that stops running still returns 200.
"""

import uuid

import pytest

from api_client import Client, rid

OPERATOR_ONLY = [
    ("POST", "/enrollments", {"request_id": "R", "agent_name": "x", "role": "y"}),
    ("POST", "/master/lease", {"request_id": "R", "expected_epoch": 0}),
    ("POST", "/master/pause", {"request_id": "R", "lease_epoch": 0, "paused": True}),
    ("POST", "/assignments", {"request_id": "R", "ticket_id": "DEMO-1",
                              "agent_id": "agt_00000001", "reason": "r",
                              "lease_epoch": 0, "expected_version": 1}),
]


def test_no_credential_is_401(server, project):
    response = Client(server, project_id=project["id"]).get("/overview")
    assert response.status == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_a_bad_bearer_token_is_401_not_a_fallthrough(server, project,
                                                     operator_session):
    """A present-but-bad credential must not silently fall back to the cookie.

    Otherwise a revoked agent token quietly borrows whatever operator session
    the same client happens to hold.
    """
    response = Client(server, project_id=project["id"], token="tbk_not_a_token",
                      cookie=operator_session["session_token"],
                      csrf=operator_session["csrf_token"]).get("/overview")
    assert response.status == 401


def test_cross_project_access_is_403_never_404(operator):
    """404 would make project existence probeable. Frozen decision."""
    response = operator.with_(project_id="prj_notmyproject").get("/overview")
    assert response.status == 403
    assert response.json()["error"]["code"] == "forbidden_scope"


def test_a_project_that_does_not_exist_is_also_403(operator):
    """Same status for "not yours" and "not there": the point is indistinguishability."""
    assert operator.with_(project_id="prj_zzzzzzzz").get("/overview").status == 403


def test_an_agent_token_from_another_project_cannot_read_this_one(server, operator,
                                                                  enrolled):
    other = server.store.create_project("Other")
    response = enrolled["client"].with_(project_id=other["id"]).get("/overview")
    assert response.status == 403


@pytest.mark.parametrize("method,path,body", OPERATOR_ONLY)
def test_agent_tokens_cannot_take_operator_powers(enrolled, method, path, body):
    body = dict(body, request_id=rid())
    response = enrolled["client"].request(method, path, body=body)
    assert response.status == 403
    payload = response.json()["error"]
    assert payload["code"] == "agent_token_insufficient"
    # The route is named so an operator reading a log knows what was refused.
    assert payload["details"]["route"] == "{} {}".format(method, path)


def test_an_agent_token_cannot_decide_a_review(operator, enrolled, ticket):
    response = enrolled["client"].post(
        "/tickets/{}/reviews/rev_00000001/decision".format(ticket["id"]),
        {"request_id": rid(), "expected_version": 1, "decision": "accept",
         "evidence_sha": "a" * 40})
    assert response.status == 403
    assert response.json()["error"]["code"] == "agent_token_insufficient"


def test_missing_csrf_token_is_refused(operator):
    response = operator.with_(csrf=None).post("/tickets", {
        "request_id": rid(), "title": "t", "outcome": "o",
        "acceptance": [{"text": "a"}]})
    assert response.status == 403
    assert response.json()["error"]["code"] == "forbidden_scope"


def test_wrong_csrf_token_is_refused(operator):
    response = operator.with_(csrf="not-the-token").post("/tickets", {
        "request_id": rid(), "title": "t", "outcome": "o",
        "acceptance": [{"text": "a"}]})
    assert response.status == 403


def test_a_cross_site_origin_is_refused_even_with_the_right_csrf(operator):
    """The pair matters: a token leaked into a page still needs an allowed origin."""
    response = operator.with_(origin="https://evil.example").post("/tickets", {
        "request_id": rid(), "title": "t", "outcome": "o",
        "acceptance": [{"text": "a"}]})
    assert response.status == 403
    assert "Origin" in response.json()["error"]["message"]


def test_an_unsafe_cookie_request_with_no_origin_at_all_is_refused(operator):
    """Absent Origin is a refusal, not a benefit of the doubt.

    A browser sends Origin on every unsafe cross-origin request and on
    same-origin fetch; a request without one is something else, and something
    else should not be able to spend a cookie.
    """
    response = operator.with_(origin=None).post("/tickets", {
        "request_id": rid(), "title": "t", "outcome": "o",
        "acceptance": [{"text": "a"}]})
    assert response.status == 403


def test_safe_methods_need_no_csrf(operator):
    assert operator.with_(csrf=None, origin=None).get("/overview").status == 200


def test_agent_tokens_need_no_csrf_or_origin(enrolled, ticket):
    """A bearer token is not attached by a browser to a cross-site request."""
    response = enrolled["client"].with_(csrf=None, origin=None).post(
        "/tickets/{}/claim".format(ticket["id"]),
        {"request_id": rid(), "expected_version": ticket["version"],
         "session_id": enrolled["session_id"]})
    assert response.status == 200


def test_the_project_header_is_required(operator):
    response = operator.with_(project_id=None).get("/overview")
    assert response.status == 400
    assert response.json()["error"]["code"] == "malformed_request"


def test_a_malformed_project_header_is_400(operator):
    assert operator.with_(project_id="not-a-project").get("/overview").status == 400


def test_an_operator_cannot_claim_through_a_runtime_session(operator, ticket,
                                                            enrolled):
    """Claiming acts through a session lease, which an operator does not have."""
    response = operator.post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]})
    assert response.status == 403
    assert response.json()["error"]["code"] == "forbidden_scope"


def test_an_agent_cannot_post_a_hook_event_for_another_agent(operator, enrolled,
                                                             server, project):
    second = operator.post("/enrollments", {
        "request_id": rid(), "agent_name": "backend-2", "role": "backend"})
    other_id = second.json()["agent"]["id"]
    response = enrolled["client"].post("/hook-events", {
        "request_id": rid(),
        "event": {"event_id": "hev_" + uuid.uuid4().hex, "agent_id": other_id,
                  "session_id": enrolled["session_id"], "kind": "session_start",
                  "occurred_at": "2026-09-06T14:32:00Z"}})
    assert response.status == 403


def test_unknown_routes_are_a_contract_shaped_404(operator):
    response = operator.get("/nope")
    assert response.status == 404
    assert response.json()["error"]["code"] == "not_found"


def test_a_wrong_method_on_a_known_route_is_also_404(operator):
    """405 is not in the frozen status enum, so it cannot be the answer."""
    assert operator.delete("/overview").status == 404


def test_other_lanes_routes_say_who_owns_them(operator):
    response = operator.get("/messages")
    assert response.status == 404
    assert response.json()["error"]["details"]["owner_ticket"] == "T-187"


def test_an_unexpected_failure_becomes_a_response_not_a_dropped_connection(
        server, operator, monkeypatch):
    """A bug in this server must still answer.

    Letting an exception escape drops the connection, and a dashboard cannot
    tell that apart from the board being down. Noted honestly in
    docs/api-notes.md: this body cannot validate against `ErrorResponse`,
    because the frozen status enum has no member for "we failed".
    """
    def boom(*args, **kwargs):
        raise RuntimeError("secret-bearing detail /Users/someone/token")

    monkeypatch.setattr(server.store, "list_tickets", boom)
    response = operator.get("/tickets")
    assert response.status == 500
    payload = response.json()["error"]
    assert payload["code"] == "internal_error"
    # The message carries no exception text: a traceback string can contain a
    # path, a query or a credential.
    assert "secret-bearing" not in payload["message"]
    assert "/Users/" not in payload["message"]


def test_a_session_id_already_in_use_is_refused_before_the_code_is_spent(
        server, project, operator, enrolled):
    """Otherwise a collision burns a single-use enrollment code for nothing."""
    created = operator.post("/enrollments", {
        "request_id": rid(), "agent_name": "backend-9", "role": "backend"})
    code = created.json()["code"]
    anon = Client(server, project_id=project["id"])
    collided = anon.post("/sessions", {
        "request_id": rid(), "code": code,
        "session_id": enrolled["session_id"]})
    assert collided.status == 400
    assert collided.json()["error"]["details"]["rejected_fields"] == ["session_id"]
    # The code survived, so the agent can retry with a fresh session id.
    retried = anon.post("/sessions", {
        "request_id": rid(), "code": code, "session_id": "ses_freshaaa"})
    assert retried.status == 201


# ------------------------------------------------------- the displaced owner
#
# From the 12:30Z planner note, which came from a live incident on the Steer
# board: T-202 was reassigned twice inside 60s and the displaced owner kept
# working for ten minutes because nothing ever refused it. "Reassignment" has
# no route in this lane -- the master loop that performs it is T-182 -- so
# these tests reassign through the store, the way that loop will, and then
# exercise the API surface that has to refuse.


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
            "client": Client(server, project_id=project["id"],
                             token=payload["token"], origin=None)}


def _reassign(server, ticket_id, agent):
    """What the T-182 master loop does: hand the ticket to somebody else."""
    server.store.conn.execute(
        "UPDATE tickets SET owner = ?, owner_session = ?, version = version + 1"
        " WHERE id = ?", (agent["agent_id"], agent["session_id"], ticket_id))
    server.store.conn.commit()


def test_a_displaced_owner_cannot_post_an_update(server, operator, project,
                                                 enrolled, ticket):
    """403 forbidden_scope -- never 404, never a silent 201.

    404 would be wrong: the ticket plainly exists and the agent can still read
    it. A silent 201 is the incident itself.
    """
    claimed = enrolled["client"].post(
        "/tickets/{}/claim".format(ticket["id"]),
        {"request_id": rid(), "expected_version": ticket["version"],
         "session_id": enrolled["session_id"]})
    assert claimed.status == 200

    successor = _enroll(server, operator, project, "backend-2")
    _reassign(server, ticket["id"], successor)

    refused = enrolled["client"].post(
        "/tickets/{}/updates".format(ticket["id"]),
        {"request_id": rid(), "body": "still working on the widget",
         "next_step": "keep going", "session_id": enrolled["session_id"]})
    assert refused.status == 403
    assert refused.json()["error"]["code"] == "forbidden_scope"
    # Refused means refused: nothing was appended to the trail.
    assert server.store.list_updates(project["id"], ticket["id"]) == []


def test_a_displaced_owner_is_refused_on_reviews_and_blocked_too(
        server, operator, project, enrolled, ticket):
    """The other two ticket writes the ruling names, on the same boundary."""
    enrolled["client"].post(
        "/tickets/{}/claim".format(ticket["id"]),
        {"request_id": rid(), "expected_version": ticket["version"],
         "session_id": enrolled["session_id"]})
    successor = _enroll(server, operator, project, "backend-2")
    _reassign(server, ticket["id"], successor)
    current = server.store.get_ticket(project["id"], ticket["id"])

    review = enrolled["client"].post(
        "/tickets/{}/reviews".format(ticket["id"]),
        {"request_id": rid(), "expected_version": current["version"],
         "evidence": {"branch": "b", "sha": "a" * 40}})
    assert review.status == 403
    assert review.json()["error"]["code"] == "forbidden_scope"

    blocked = enrolled["client"].post(
        "/tickets/{}/blocked".format(ticket["id"]),
        {"request_id": rid(), "expected_version": current["version"],
         "blocked": True, "reason": "waiting on infra"})
    assert blocked.status == 403
    assert blocked.json()["error"]["code"] == "forbidden_scope"


def test_a_stale_session_of_the_owner_is_kept_and_superseded(
        server, project, enrolled, ticket, operator):
    """The other path, and it deliberately does NOT 403.

    Same *agent*, older *session*. The store keeps this update and flags it
    `superseded`, because a displaced session's account of what it was doing is
    the most useful thing in the trail after a takeover. Only a different agent
    gets refused. Losing this distinction is how the 403 above would turn into
    a data-loss bug.
    """
    enrolled["client"].post(
        "/tickets/{}/claim".format(ticket["id"]),
        {"request_id": rid(), "expected_version": ticket["version"],
         "session_id": enrolled["session_id"]})
    server.store.conn.execute(
        "UPDATE tickets SET owner_session = ? WHERE id = ?",
        ("ses_newersession", ticket["id"]))
    server.store.conn.commit()

    late = enrolled["client"].post(
        "/tickets/{}/updates".format(ticket["id"]),
        {"request_id": rid(), "body": "what I was doing before the takeover",
         "next_step": "hand over", "session_id": enrolled["session_id"]})
    assert late.status == 201
    assert late.json()["superseded"] is True
    kept = server.store.list_updates(project["id"], ticket["id"])
    assert [u["body"] for u in kept] == ["what I was doing before the takeover"]


def test_a_stale_claim_surfaces_as_a_version_conflict(server, operator,
                                                      project, enrolled, ticket):
    """The ruling's second named case: a stale claim, not a stale write.

    The displaced agent re-reads and tries to claim on the version it last saw.
    That is a version conflict carrying both numbers, so one round trip tells
    it what actually happened -- not forbidden_scope, which would describe the
    wrong problem.
    """
    stale_version = ticket["version"]
    successor = _enroll(server, operator, project, "backend-2")
    successor["client"].post(
        "/tickets/{}/claim".format(ticket["id"]),
        {"request_id": rid(), "expected_version": stale_version,
         "session_id": successor["session_id"]})

    stale = enrolled["client"].post(
        "/tickets/{}/claim".format(ticket["id"]),
        {"request_id": rid(), "expected_version": stale_version,
         "session_id": enrolled["session_id"]})
    assert stale.status == 409
    error = stale.json()["error"]
    assert error["code"] == "ticket_version_conflict"
    # Both numbers, so the agent re-reads once and knows where it stands
    # instead of guessing at a bare 409.
    assert error["details"]["expected_version"] == stale_version
    assert error["details"]["actual_version"] > stale_version
