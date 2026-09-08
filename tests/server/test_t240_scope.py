"""T-240: decide_review, revoke_session_lease and get_agent must not treat a
caller-supplied id as globally unique -- a wrong project has to look exactly
like "does not exist," both in status and in what actually happens to the
data.
"""

import uuid

import pytest

from api_client import Client, rid


def _enroll(server, operator, project, name="atk-agent"):
    created = operator.post("/enrollments", {
        "request_id": rid(), "agent_name": name, "role": "backend",
        "connection_mode": "managed"})
    assert created.status == 201, created.json()
    session_id = "ses_" + uuid.uuid4().hex[:8]
    exchanged = Client(server, project_id=project["id"]).post("/sessions", {
        "request_id": rid(), "code": created.json()["code"],
        "session_id": session_id})
    assert exchanged.status == 201, exchanged.json()
    payload = exchanged.json()
    return {"agent_id": payload["agent"]["id"], "session_id": session_id,
            "token": payload["token"],
            "client": Client(server, project_id=project["id"],
                             token=payload["token"], origin=None)}


def test_decide_review_cannot_mutate_a_different_projects_review_via_a_colliding_ticket_id(
        server):
    """Setup and attack call are sonnet-qa's exact T-225 reproduction
    (test_decide_review_can_mutate_a_different_projects_review_via_a_
    colliding_ticket_id, tests/server/test_t225_adversarial.py) reused
    unchanged, per the ticket's 'do not re-derive the repro'. Only the win
    condition is inverted, because the whole point of the fix this test
    guards is that the attack which used to succeed here (200, project B's
    review rejected using only project A's credential) must now fail --
    with the SAME status a nonexistent review returns, per acceptance #2,
    and with project B's review provably untouched.
    """
    project_a = server.store.create_project("Demo")
    project_b = server.store.create_project("Demo")
    op_a_session = server.bootstrap_operator(project_a["id"])
    op_b_session = server.bootstrap_operator(project_b["id"])
    op_a = Client(server, project_id=project_a["id"],
                 cookie=op_a_session["session_token"], csrf=op_a_session["csrf_token"])
    op_b = Client(server, project_id=project_b["id"],
                 cookie=op_b_session["session_token"], csrf=op_b_session["csrf_token"])

    ticket_a = op_a.post("/tickets", {"request_id": rid(), "title": "A1",
                                      "outcome": "o", "acceptance": [{"text": "a"}]}).json()
    ticket_b = op_b.post("/tickets", {"request_id": rid(), "title": "B1",
                                      "outcome": "o", "acceptance": [{"text": "a"}]}).json()
    assert ticket_a["id"] == ticket_b["id"], (
        "test setup assumption: same-named projects mint colliding ticket "
        "ids -- {} vs {}".format(ticket_a["id"], ticket_b["id"])
    )

    agent_b = _enroll(server, op_b, project_b, "victim-agent")
    claimed = agent_b["client"].post(
        "/tickets/{}/claim".format(ticket_b["id"]),
        {"request_id": rid(), "expected_version": ticket_b["version"],
         "session_id": agent_b["session_id"]})
    assert claimed.status == 200, claimed.json()
    review = agent_b["client"].post(
        "/tickets/{}/reviews".format(ticket_b["id"]),
        {"request_id": rid(), "expected_version": claimed.json()["version"],
         "evidence": {"repository": "acme/b", "sha": "a" * 40, "branch": "main"}})
    assert review.status == 201, review.json()
    review_id = review.json()["id"]
    sha = review.json()["evidence"]["sha"]

    attack = op_a.post(
        "/tickets/{}/reviews/{}/decision".format(ticket_a["id"], review_id),
        {"request_id": rid(), "expected_version": ticket_a["version"],
         "decision": "reject", "evidence_sha": sha})

    # Parity target: a review_id that is well-formed but does not exist at
    # all, attacked the same way, in the same (project A, ticket A) context.
    nonexistent = op_a.post(
        "/tickets/{}/reviews/rev_{}/decision".format(ticket_a["id"], "0" * 24),
        {"request_id": rid(), "expected_version": ticket_a["version"],
         "decision": "reject", "evidence_sha": sha})

    assert attack.status == nonexistent.status == 404, (attack.json(), nonexistent.json())
    assert attack.json()["error"]["code"] == nonexistent.json()["error"]["code"] \
        == "not_found"
    # Not just the status: the message and details shape must match too, or
    # the oracle just moved into the body instead of the status line.
    assert attack.json()["error"]["message"] == nonexistent.json()["error"]["message"]

    untouched = server.store.get_review(project_b["id"], review_id)
    assert untouched["state"] == "requested", (
        "project B's review must be untouched by an operator who was never "
        "authorized for project B at all"
    )


def test_revoke_session_lease_cross_project_matches_nonexistent(server):
    """Acceptance #3: revoke_session_lease gets the same treatment.

    Before the fix, a wrong-project agent_id raised ForbiddenScope (403)
    while a truly nonexistent one raised NotFound (404) -- an attacker could
    tell "exists in another project" from "does not exist" purely from the
    status code, even though the revoke itself was correctly blocked either
    way. Both must now be indistinguishable.
    """
    project_a = server.store.create_project("Demo")
    project_b = server.store.create_project("Demo")
    op_a_session = server.bootstrap_operator(project_a["id"])
    op_b_session = server.bootstrap_operator(project_b["id"])
    op_a = Client(server, project_id=project_a["id"],
                 cookie=op_a_session["session_token"], csrf=op_a_session["csrf_token"])
    op_b = Client(server, project_id=project_b["id"],
                 cookie=op_b_session["session_token"], csrf=op_b_session["csrf_token"])

    victim = _enroll(server, op_b, project_b, "victim-agent")

    cross_project = op_a.delete(
        "/agents/{}/session-lease".format(victim["agent_id"]),
        {"request_id": rid(), "expected_version": 1, "note": "n"})
    nonexistent = op_a.delete(
        "/agents/agt_{}/session-lease".format("0" * 24),
        {"request_id": rid(), "expected_version": 1, "note": "n"})

    assert cross_project.status == nonexistent.status == 404, (
        cross_project.json(), nonexistent.json())
    assert cross_project.json()["error"]["code"] == nonexistent.json()["error"]["code"] \
        == "not_found"
    assert cross_project.json()["error"]["message"] == \
        nonexistent.json()["error"]["message"]

    # And the victim's own session is untouched.
    still_alive = victim["client"].get("/overview")
    assert still_alive.status == 200, still_alive.json()


def test_get_agent_cross_project_matches_nonexistent(server):
    """Acceptance #3, store layer: get_agent(agent_id, project_id) must
    raise the identical NotFound whether the id belongs to another project
    or does not exist at all -- the same closure as __review, one layer
    down. project_id stays optional so trusted internal callers (agent_id
    minted or already verified in the caller's own project) are unaffected;
    see the callers audited in the review notes.
    """
    from ticket_board.storage.errors import NotFound

    project_a = server.store.create_project("Demo")
    project_b = server.store.create_project("Demo")
    op_b_session = server.bootstrap_operator(project_b["id"])
    op_b = Client(server, project_id=project_b["id"],
                 cookie=op_b_session["session_token"], csrf=op_b_session["csrf_token"])
    victim = _enroll(server, op_b, project_b, "victim-agent")

    with pytest.raises(NotFound) as cross_project:
        server.store.get_agent(victim["agent_id"], project_a["id"])
    with pytest.raises(NotFound) as nonexistent:
        server.store.get_agent("agt_" + "0" * 24, project_a["id"])

    assert cross_project.value.status == nonexistent.value.status == 404
    assert cross_project.value.message == nonexistent.value.message

    # Unscoped (no project_id): still resolves, for trusted internal callers.
    assert server.store.get_agent(victim["agent_id"])["id"] == victim["agent_id"]
