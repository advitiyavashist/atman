"""Review submission and decision: pinned evidence and reviewer separation."""

import uuid

import pytest

from api_client import Client, rid

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
OTHER_SHA = "b2c3d4e5f60718293a4b5c6d7e8f90123456789a"


def evidence(sha=SHA, status="passed"):
    return {"repository": "advitiyavashist/tickets", "branch": "feature/demo-13",
            "sha": sha, "pr_url": None,
            "checks": [{"name": "pytest", "status": status, "details_url": None}]}


@pytest.fixture()
def submitted(operator, enrolled, ticket):
    """A claimed ticket with a review awaiting a decision."""
    claimed = enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]}).json()
    review = enrolled["client"].post("/tickets/{}/reviews".format(ticket["id"]), {
        "request_id": rid(), "expected_version": claimed["version"],
        "evidence": evidence(), "notes": "Covered by tests."})
    assert review.status == 201, review.json()
    detail = operator.get("/tickets/" + ticket["id"]).json()
    return {"review": review.json(), "ticket": detail["ticket"]}


def test_submitting_moves_the_ticket_to_review(operator, submitted, ticket):
    assert submitted["ticket"]["state"] == "review"
    assert submitted["review"]["state"] == "requested"
    assert submitted["review"]["evidence"]["sha"] == SHA


def test_only_the_owner_may_submit(server, operator, project, enrolled, ticket):
    claimed = enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]}).json()
    other = _enroll(server, operator, project, "backend-2")
    response = other["client"].post("/tickets/{}/reviews".format(ticket["id"]), {
        "request_id": rid(), "expected_version": claimed["version"],
        "evidence": evidence()})
    assert response.status == 403


def test_an_unclaimed_ticket_cannot_go_to_review(operator, ticket):
    response = operator.post("/tickets/{}/reviews".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "evidence": evidence()})
    assert response.status == 422
    assert response.json()["error"]["code"] == "invalid_state_transition"


def test_a_stale_version_on_submission_is_409(enrolled, ticket):
    enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]})
    response = enrolled["client"].post("/tickets/{}/reviews".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "evidence": evidence()})
    assert response.status == 409


def test_a_malformed_sha_never_reaches_the_store(enrolled, ticket):
    """Evidence nobody can compare against later is the failure the pin prevents."""
    enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]})
    response = enrolled["client"].post("/tickets/{}/reviews".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"] + 1,
        "evidence": {"branch": "b", "sha": "not-a-sha"}})
    assert response.status == 400
    assert response.json()["error"]["details"]["rejected_fields"] == ["evidence.sha"]


def test_accepting_pins_the_sha(operator, submitted, ticket):
    accepted = operator.post("/tickets/{}/reviews/{}/decision".format(
        ticket["id"], submitted["review"]["id"]), {
        "request_id": rid(), "expected_version": submitted["ticket"]["version"],
        "decision": "accept", "evidence_sha": SHA, "notes": "Matches."})
    assert accepted.status == 200
    assert accepted.json()["state"] == "accepted"
    assert operator.get("/tickets/" + ticket["id"]).json()["ticket"]["state"] == "done"


def test_a_different_sha_is_refused(operator, submitted, ticket):
    """The branch tip may have moved; the reviewed artifact did not."""
    response = operator.post("/tickets/{}/reviews/{}/decision".format(
        ticket["id"], submitted["review"]["id"]), {
        "request_id": rid(), "expected_version": submitted["ticket"]["version"],
        "decision": "accept", "evidence_sha": OTHER_SHA})
    assert response.status == 422
    assert response.json()["error"]["code"] == "invalid_review_evidence"


def test_a_failing_check_blocks_acceptance(operator, enrolled, ticket):
    claimed = enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]}).json()
    review = enrolled["client"].post("/tickets/{}/reviews".format(ticket["id"]), {
        "request_id": rid(), "expected_version": claimed["version"],
        "evidence": evidence(status="failed")}).json()
    version = operator.get("/tickets/" + ticket["id"]).json()["ticket"]["version"]
    response = operator.post("/tickets/{}/reviews/{}/decision".format(
        ticket["id"], review["id"]), {
        "request_id": rid(), "expected_version": version, "decision": "accept",
        "evidence_sha": SHA})
    assert response.status == 422
    assert response.json()["error"]["details"]["failed_checks"] == ["pytest"]


def test_rejection_returns_the_ticket_to_claimed_with_notes(operator, submitted,
                                                             ticket):
    rejected = operator.post("/tickets/{}/reviews/{}/decision".format(
        ticket["id"], submitted["review"]["id"]), {
        "request_id": rid(), "expected_version": submitted["ticket"]["version"],
        "decision": "reject", "notes": "Needs a test for the last page.",
        "evidence_sha": SHA})
    assert rejected.status == 200
    assert rejected.json()["state"] == "rejected"
    assert rejected.json()["decision_notes"] == "Needs a test for the last page."
    assert operator.get("/tickets/" + ticket["id"]).json()["ticket"]["state"] == \
        "claimed"


def test_the_submitter_cannot_decide_their_own_review(server, project, operator,
                                                       ticket):
    """Acceptance by the author is not review, whatever role the author wears.

    Set up so the *operator* is the submitter, which is the only way to get a
    submitter that can also reach the decision route at all.
    """
    from ticket_board.server.auth import in_seconds

    server.store.conn.execute(
        "UPDATE tickets SET state = 'claimed' WHERE project_id = ? AND id = ?",
        (project["id"], ticket["id"]))
    review = operator.post("/tickets/{}/reviews".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "evidence": evidence()})
    assert review.status == 201, review.json()
    version = operator.get("/tickets/" + ticket["id"]).json()["ticket"]["version"]
    response = operator.post("/tickets/{}/reviews/{}/decision".format(
        ticket["id"], review.json()["id"]), {
        "request_id": rid(), "expected_version": version, "decision": "accept",
        "evidence_sha": SHA})
    assert response.status == 403
    assert response.json()["error"]["code"] == "forbidden_scope"


def test_a_second_operator_may_decide(server, project, operator, submitted,
                                       ticket):
    second = server.bootstrap_operator(project["id"], display_name="Reviewer")
    reviewer = Client(server, project_id=project["id"],
                      cookie=second["session_token"], csrf=second["csrf_token"])
    accepted = reviewer.post("/tickets/{}/reviews/{}/decision".format(
        ticket["id"], submitted["review"]["id"]), {
        "request_id": rid(), "expected_version": submitted["ticket"]["version"],
        "decision": "accept", "evidence_sha": SHA})
    assert accepted.status == 200


def test_a_decided_review_cannot_be_decided_again(operator, submitted, ticket):
    body = {"request_id": rid(),
            "expected_version": submitted["ticket"]["version"],
            "decision": "accept", "evidence_sha": SHA}
    assert operator.post("/tickets/{}/reviews/{}/decision".format(
        ticket["id"], submitted["review"]["id"]), body).status == 200
    version = operator.get("/tickets/" + ticket["id"]).json()["ticket"]["version"]
    again = operator.post("/tickets/{}/reviews/{}/decision".format(
        ticket["id"], submitted["review"]["id"]),
        dict(body, request_id=rid(), expected_version=version))
    assert again.status == 422


def test_a_review_id_from_another_ticket_is_404(operator, submitted, ticket):
    other = operator.post("/tickets", {
        "request_id": rid(), "title": "Other", "outcome": "o",
        "acceptance": [{"text": "a"}]}).json()
    response = operator.post("/tickets/{}/reviews/{}/decision".format(
        other["id"], submitted["review"]["id"]), {
        "request_id": rid(), "expected_version": other["version"],
        "decision": "accept", "evidence_sha": SHA})
    assert response.status == 404


def test_available_actions_do_not_offer_the_submitter_an_accept(operator,
                                                                 enrolled,
                                                                 ticket):
    claimed = enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]}).json()
    enrolled["client"].post("/tickets/{}/reviews".format(ticket["id"]), {
        "request_id": rid(), "expected_version": claimed["version"],
        "evidence": evidence()})
    # The operator did not submit, so they are offered the decision...
    assert "accept" in operator.get(
        "/tickets/" + ticket["id"]).json()["available_actions"]
    # ... and the submitting agent is never offered it.
    agent_view = enrolled["client"].get("/tickets/" + ticket["id"]).json()
    assert "accept" not in agent_view["available_actions"]


def _enroll(server, operator, project, name):
    created = operator.post("/enrollments", {
        "request_id": rid(), "agent_name": name, "role": "backend"})
    session_id = "ses_" + uuid.uuid4().hex[:8]
    payload = Client(server, project_id=project["id"]).post("/sessions", {
        "request_id": rid(), "code": created.json()["code"],
        "session_id": session_id}).json()
    return {"agent_id": payload["agent"]["id"], "session_id": session_id,
            "client": Client(server, project_id=project["id"],
                             token=payload["token"], origin=None)}


def test_a_stale_ticket_version_on_the_decision_is_409(operator, submitted,
                                                        ticket):
    """The reviewer read the ticket, then something moved it.

    The decision carries `expected_version` for the same reason every other
    mutation does; the store does not check it, so the route must.
    """
    response = operator.post("/tickets/{}/reviews/{}/decision".format(
        ticket["id"], submitted["review"]["id"]), {
        "request_id": rid(),
        "expected_version": submitted["ticket"]["version"] - 1,
        "decision": "accept", "evidence_sha": SHA})
    assert response.status == 409
    assert response.json()["error"]["code"] == "ticket_version_conflict"
    # And it changed nothing.
    assert operator.get("/tickets/" + ticket["id"]).json()["ticket"]["state"] == \
        "review"
