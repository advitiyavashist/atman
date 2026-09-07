"""Credentials, scope and CSRF -- the trust boundary of the board.

Each test names the property it defends, because the failure mode for most of
them is silent: a check that stops running still returns 200.
"""

import re
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


def _concrete_path(pattern):
    """`^/messages/(?P<message_id>[^/]+)/deliveries$` -> a real, well-shaped URL.

    The placeholder ids are syntactically valid for their prefix so that a
    route which validates its path parameter reaches its own handler instead of
    bouncing at the router -- either answer is acceptable to the caller above,
    but reaching the handler is the stronger exercise.
    """
    body = pattern.lstrip("^").rstrip("$")
    return re.sub(r"\(\?P<(\w+)>\[\^/\]\+\)",
                  lambda m: _PLACEHOLDER_IDS.get(m.group(1), "x"), body)


_PLACEHOLDER_IDS = {
    "ticket_id": "DEMO-1",
    "review_id": "rev_00000000",
    "agent_id": "agt_00000000",
    "channel_id": "chn_00000000",
    "message_id": "msg_00000000",
    "run_id": "run_00000000",
}


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


def test_no_served_route_is_still_an_unbuilt_lane_stub(operator, enrolled):
    """Nothing this build routes may answer with a "not my lane" 404.

    HISTORY, because this test used to assert the opposite and the reversal is
    the point. While lanes were landing one at a time, `_not_this_lane` made a
    contract-published-but-unimplemented route return a 404 carrying
    `details.owner_ticket`, so the console could tell "not built yet" from
    "broken". T-187 (messaging) and T-188 (runners) were the last two holders,
    and with both landed the mechanism has no remaining caller and is gone.

    The original assertion -- `/runners/jobs` is a T-188 stub -- was written on
    T-187's branch when that was true, and it went stale the moment T-188
    merged: the route now exists and answers 400 for a missing `runner_id`.
    Deleting a test whose premise expired loses the guard, so it is inverted
    instead. The property is the same one, stated for the finished build: a
    route the server routes must actually be served. If someone reintroduces a
    stub, or lands a new lane placeholder and forgets to replace it, this
    fails.

    It is a sweep rather than one probe because a single named route is exactly
    what went stale here. Bodies are omitted deliberately -- most of these will
    answer 400 or 403 and that is fine; the only thing asserted is that the
    refusal is a real one and not a placeholder.
    """
    from ticket_board.server.app import _ROUTE_TABLE

    checked = []
    for method, pattern, handler, _auth, _csrf in _ROUTE_TABLE:
        path = _concrete_path(pattern)
        if path == "/events":
            # SSE: the stream is open-ended and never returns a Response here.
            # Its own ACL and framing are covered by test_events_stream.py.
            continue
        for client in (operator, enrolled["client"]):
            response = client.request(method, path)
            details = (response.json().get("error") or {}).get("details") or {}
            assert "owner_ticket" not in details, (
                "{} {} (handler {}) still answers as an unbuilt-lane stub "
                "owned by {}".format(method, path, handler,
                                     details.get("owner_ticket")))
        checked.append((method, path))

    # A sweep that silently swept nothing would pass forever.
    assert len(checked) >= 30, checked
    assert ("GET", "/runners/jobs") in checked
    assert ("GET", "/messages") in checked


def test_the_messaging_routes_are_no_longer_stubs(operator):
    """The inverse of the test above, so the two cannot both rot silently."""
    response = operator.get("/messages", query="channel_id=chn_00000000")
    assert response.status != 404 or \
        "owner_ticket" not in (response.json()["error"].get("details") or {})


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
# Reassignment without a refusal path lets a displaced owner keep writing.
# These tests reassign through the store and then exercise the API surface
# that has to refuse.


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
         "evidence": {"repository": "acme/repo", "branch": "b", "sha": "a" * 40}})
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


def test_omitting_session_id_is_not_a_healthy_update(
        server, project, enrolled, ticket, operator):
    """T-239: session_id is OPTIONAL in CreateUpdateRequest, so a client can
    disable the superseded safeguard just by leaving the field out -- and the
    displaced session is exactly the caller least likely to send one. Absent
    must land as a third, unattributed case: kept, but denied the two effects
    a clean update gets (last_progress_at, next_step), and audited under its
    own action rather than folded into a plain `ticket.update`.
    """
    enrolled["client"].post(
        "/tickets/{}/claim".format(ticket["id"]),
        {"request_id": rid(), "expected_version": ticket["version"],
         "session_id": enrolled["session_id"]})
    before = operator.get("/tickets/" + ticket["id"]).json()["ticket"]

    update = enrolled["client"].post(
        "/tickets/{}/updates".format(ticket["id"]),
        {"request_id": rid(), "body": "still going",
         "next_step": "should not land"})
    assert update.status == 201
    assert update.json()["superseded"] is False, "the boolean has no third state"

    after = operator.get("/tickets/" + ticket["id"]).json()["ticket"]
    assert after["last_progress_at"] == before["last_progress_at"], \
        "an unattributed update must not count as progress"
    assert after["next_step"] == before["next_step"], \
        "an unattributed update must not overwrite next_step"

    actions = [e["action"] for e in server.store.audit_trail(project["id"])
              if e["subject_id"] == ticket["id"]]
    assert "ticket.update.unattributed" in actions
    assert "ticket.update" not in actions, \
        "must not read as an ordinary healthy progress update"


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


# ---------------------------------------------------------------- T-236
# A route declared `auth=NONE` proves the caller some other way -- the
# enrollment code in the body, for `POST /sessions`. Authenticating it anyway
# breaks the one flow that needs it most: recovery. An agent whose lease was
# revoked has, by construction, a dead bearer token in its configured headers,
# and re-enrolment is how it is supposed to come back.


def _revoked_agent(server, operator, project, name="recover-1"):
    """An agent whose session lease -- and therefore whose token -- is revoked."""
    agent = _enroll(server, operator, project, name)
    listed = operator.get("/agents").json()
    rows = listed.get("agents") or listed.get("items") or []
    version = next(r["version"] for r in rows if r["id"] == agent["agent_id"])
    revoked = operator.delete(
        "/agents/{}/session-lease".format(agent["agent_id"]),
        {"request_id": rid(), "expected_version": version, "note": "operator revoked"})
    assert revoked.status == 200, revoked.json()
    return agent


def _fresh_code(operator, name):
    created = operator.post("/enrollments", {
        "request_id": rid(), "agent_name": name, "role": "backend",
        "connection_mode": "managed"})
    assert created.status == 201, created.json()
    return created.json()["code"]


def test_a_revoked_agent_can_re_enrol_with_its_dead_token_still_attached(
        server, operator, project):
    """Acceptance 1: the recovery path, with the natural client implementation.

    The obvious way to write an adapter is to configure the Authorization header
    once and reuse it. That client is exactly the one that cannot recover if
    `POST /sessions` authenticates: its token died with the lease, so the call
    that is supposed to give it a new one is refused for holding the old one.
    """
    dead = _revoked_agent(server, operator, project)
    assert dead["client"].get("/tickets").status == 401, "precondition: token is dead"

    exchanged = Client(server, project_id=project["id"],
                       token=dead["client"].token).post("/sessions", {
                           "request_id": rid(),
                           "code": _fresh_code(operator, "recover-2"),
                           "session_id": "ses_" + uuid.uuid4().hex[:8]})
    assert exchanged.status == 201, exchanged.json()
    assert exchanged.json()["token"] != dead["client"].token


@pytest.mark.parametrize("label,extra", [
    ("absent", {}),
    ("unknown-bearer", {"Authorization": "Bearer tbk_not_a_token"}),
    ("empty-bearer", {"Authorization": "Bearer "}),
    ("not-bearer", {"Authorization": "Basic aGk6dGhlcmU="}),
    ("no-scheme", {"Authorization": "tbk_not_a_token"}),
    ("dead-cookie", {"Cookie": "tb_session=not-a-session"}),
])
def test_post_sessions_ignores_whatever_is_in_the_credential_headers(
        server, operator, project, label, extra):
    """Acceptance 2: absent, malformed and unknown credentials all still work.

    The code in the body is the proof. Nothing about the headers may change the
    answer -- including a header that is not a bearer token at all, which
    `authenticate_all` treats as its own kind of refusal.
    """
    exchanged = Client(server, project_id=project["id"]).post(
        "/sessions",
        {"request_id": rid(), "code": _fresh_code(operator, "hdr-" + label),
         "session_id": "ses_" + uuid.uuid4().hex[:8]},
        headers=extra)
    assert exchanged.status == 201, exchanged.json()


def test_a_bad_code_is_still_refused_on_an_unauthenticated_route(server, project):
    """Ignoring the header must not mean ignoring the proof that does count."""
    response = Client(server, project_id=project["id"]).post("/sessions", {
        "request_id": rid(), "code": "not-a-real-code",
        "session_id": "ses_" + uuid.uuid4().hex[:8]})
    assert response.status == 422
    assert response.json()["error"]["code"] == "enrollment_code_invalid"


def test_no_auth_none_route_rejects_a_present_but_invalid_credential(
        server, project, operator_session):
    """Acceptance 3: pin the rule at the seam, not at the one route that broke.

    This is a mismatch between a route's *declared* auth level and what
    `_authorize` does, so the test walks the route table rather than naming
    `/sessions`. A new `auth=NONE` route that reintroduces the bug fails here
    without anyone remembering to write a test for it.
    """
    from ticket_board.server.app import NONE, _ROUTE_TABLE

    none_routes = [(m, p) for m, p, _h, a, _s in _ROUTE_TABLE if a == NONE]
    assert none_routes, "the route table has no auth=NONE routes to check"

    for method, pattern in none_routes:
        path = pattern.strip("^$")
        assert "(?P<" not in path, "T-236 needs a literal path for {}".format(path)
        for bad in (Client(server, project_id=project["id"], token="tbk_dead"),
                    Client(server, project_id=project["id"], cookie="dead-cookie")):
            response = bad.request(method, path, body={})
            assert response.status != 401, (
                "{} {} is declared auth=NONE but rejected a present-but-invalid "
                "credential with 401".format(method, path))


def test_no_auth_none_route_can_never_resolve_a_principal(server, operator, project):
    """T-264, pinning the RULE the test above cannot state.

    The test above only pins "not 401" -- the symptom T-236 fixed, not the
    rule it stated ("no credential is read"). A future NONE route that reads
    `ctx.principal` and acts on it answers 200 and satisfies that assertion
    even when the principal belongs to someone else's project entirely --
    T-249 proved this against already-merged code by registering exactly such
    a route and watching it leak.

    `_authorize` is the one seam every route shares, present and future (its
    own docstring: "Authorization is decided in exactly one place"), so this
    pins the rule there directly rather than against today's two NONE routes,
    neither of which happens to read ctx.principal yet. A VALID credential,
    scoped to a project other than the caller's X-Project-Id, must still
    resolve to no principal at all for auth=NONE -- not a foreign one, not a
    filtered-to-None one, never resolved in the first place.
    """
    from ticket_board.server.app import NONE
    from ticket_board.server.wire import Request

    other = server.store.create_project("Other")
    other_session = server.bootstrap_operator(other["id"])
    other_operator = Client(server, project_id=other["id"],
                            cookie=other_session["session_token"],
                            csrf=other_session["csrf_token"])
    foreign = _enroll(server, other_operator, other, "t264-seam")

    request = Request("POST", "/sessions", query="", headers={
        "X-Project-Id": project["id"],
        "Authorization": "Bearer " + foreign["client"].token,
    }, body=b"{}")

    assert server._authorize(request, server.exchange_enrollment, NONE,
                             project["id"]) is None, (
        "a live, valid, foreign-project credential must not resolve to a "
        "principal on an auth=NONE route")


def test_a_revoked_token_still_fails_closed_on_routes_that_require_it(
        server, operator, project):
    """Acceptance 4: the fix must not weaken anything that actually authenticates.

    Ignoring credentials is correct only where the route asked for none. On every
    other route a dead token is still a dead token, and -- the property T-225
    verified -- it must not silently fall through to a cookie the same client
    happens to hold either.
    """
    dead = _revoked_agent(server, operator, project, "still-dead")
    token = dead["client"].token

    assert Client(server, project_id=project["id"], token=token).get(
        "/overview").status == 401
    assert Client(server, project_id=project["id"], token=token).post(
        "/tickets", {"request_id": rid(), "title": "t", "outcome": "o",
                     "acceptance": [{"text": "a"}]}).status == 401
    # ...and still no fall-through to a valid operator session on the same request.
    session = server.bootstrap_operator(project["id"])
    both = Client(server, project_id=project["id"], token=token,
                  cookie=session["session_token"], csrf=session["csrf_token"])
    assert both.get("/overview").status == 401


def test_an_expired_session_is_told_to_sign_in_again(server, project,
                                                     operator_session):
    """Acceptance 4, second half: the fix must not flatten `authenticate_all`.

    The tempting over-broad repair for T-236 is to swallow `Unauthenticated`
    around the shared call and let the generic "no credential" refusal fall out
    below. It still fails closed, so nothing about scope or fall-through
    notices -- but it silently discards the one 401 that is not interchangeable.
    An operator whose session merely aged out needs to be told to sign in again;
    answering "no valid credential" sends them looking for a bug instead.

    This is deliberately *not* in tension with the byte-identical refusals for a
    bad token and a bad cookie: those are indistinguishable because telling the
    two apart helps an attacker, whereas the holder of an expired cookie already
    knows they had one.
    """
    server.store.conn.execute(
        "UPDATE operator_sessions SET expires_at = '2000-01-01T00:00:00Z'"
        " WHERE token_hash IS NOT NULL")
    server.store.conn.commit()

    expired = Client(server, project_id=project["id"],
                     cookie=operator_session["session_token"]).get("/overview")
    assert expired.status == 401
    assert expired.json()["error"]["message"] == "Session expired. Sign in again."

    anonymous = Client(server, project_id=project["id"]).get("/overview")
    assert anonymous.json()["error"]["message"] != \
        expired.json()["error"]["message"]
