"""`request_id` replay, and the bodies the contract refuses to accept."""

import uuid

import pytest

from api_client import Client, rid


def test_replaying_a_mutation_returns_the_original_and_applies_once(
        enrolled, ticket, operator):
    key = rid()
    body = {"request_id": key, "expected_version": ticket["version"],
            "session_id": enrolled["session_id"]}
    first = enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), body)
    second = enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), body)
    assert first.status == 200 and second.status == 200
    assert first.json() == second.json()
    # Applied once: a second real claim would have bumped the version again.
    assert operator.get("/tickets/" + ticket["id"]).json()["ticket"]["version"] == \
        first.json()["version"]


def test_replaying_post_tickets_returns_the_same_ticket_not_a_409(operator):
    """T-235: create_ticket used to hash the server-minted id into the replay
    body. Since a retry always gets a fresh id from `_next_ticket_id`, the
    hash never matched and a byte-identical retry was always refused as if
    the body had changed -- which it had not.
    """
    key = rid()
    body = {"request_id": key, "title": "Replay me", "outcome": "o",
            "acceptance": [{"text": "a"}]}
    first = operator.post("/tickets", body)
    second = operator.post("/tickets", body)
    assert first.status == 201
    assert second.status == 201
    assert first.json() == second.json()
    assert first.json()["id"] == second.json()["id"]
    # Applied once: no sibling ticket was minted for the retry.
    listing = operator.get("/tickets").json()["items"]
    assert len([t for t in listing if t["title"] == "Replay me"]) == 1


def test_same_request_id_different_body_still_409_for_post_tickets(operator):
    """The replay guard itself must survive dropping ticket_id from the hash."""
    key = rid()
    first = operator.post("/tickets", {
        "request_id": key, "title": "Original", "outcome": "o",
        "acceptance": [{"text": "a"}]})
    assert first.status == 201
    second = operator.post("/tickets", {
        "request_id": key, "title": "Different", "outcome": "o",
        "acceptance": [{"text": "a"}]})
    assert second.status == 409
    assert second.json()["error"]["code"] == "request_id_reused"


def test_the_same_key_with_a_different_body_is_409(enrolled, ticket):
    key = rid()
    path = "/tickets/{}/updates".format(ticket["id"])
    enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]})
    first = enrolled["client"].post(path, {"request_id": key, "body": "one",
                                           "next_step": "n"})
    assert first.status == 201
    second = enrolled["client"].post(path, {"request_id": key, "body": "two",
                                            "next_step": "n"})
    assert second.status == 409
    assert second.json()["error"]["code"] == "request_id_reused"


def test_replaying_an_update_returns_the_original_and_applies_once(
        server, enrolled, ticket):
    """T-255: add_update had only a differing-body test; nothing asserted that
    a byte-identical retry replays instead of appending a second row.
    """
    enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]})
    key = rid()
    path = "/tickets/{}/updates".format(ticket["id"])
    body = {"request_id": key, "body": "did some things", "next_step": "n"}
    first = enrolled["client"].post(path, body)
    second = enrolled["client"].post(path, body)
    assert first.status == 201 and second.status == 201
    assert first.json() == second.json()
    count = server.store.conn.execute(
        "SELECT COUNT(*) FROM ticket_updates WHERE ticket_id = ?", (ticket["id"],)
    ).fetchone()[0]
    assert count == 1


def test_replaying_blocked_returns_the_original_and_applies_once(
        server, operator, ticket):
    """T-255: `/blocked` had no replay test at all."""
    key = rid()
    path = "/tickets/{}/blocked".format(ticket["id"])
    body = {"request_id": key, "expected_version": ticket["version"],
            "blocked": True, "reason": "waiting on T-1"}
    first = operator.post(path, body)
    second = operator.post(path, body)
    assert first.status == 200 and second.status == 200
    assert first.json() == second.json()
    detail = operator.get("/tickets/" + ticket["id"]).json()["ticket"]
    assert detail["state"] == "blocked"
    # Applied once: a second real block/unblock cycle would have moved the
    # version by two, or flipped the state back to open.
    assert detail["version"] == ticket["version"] + 1


def test_request_ids_are_scoped_per_project(server, operator, project):
    """The same key in a different project is a different request, not a replay."""
    other = server.store.create_project("Other")
    other_session = server.bootstrap_operator(other["id"])
    other_client = Client(server, project_id=other["id"],
                          cookie=other_session["session_token"],
                          csrf=other_session["csrf_token"])
    key = rid()
    body = {"request_id": key, "title": "Shared key", "outcome": "o",
            "acceptance": [{"text": "a"}]}
    first = operator.post("/tickets", body)
    second = other_client.post("/tickets", body)
    assert first.status == 201 and second.status == 201
    assert first.json()["project_id"] != second.json()["project_id"]


def test_a_non_uuid_request_id_is_400(operator):
    response = operator.post("/tickets", {
        "request_id": "not-a-uuid", "title": "t", "outcome": "o",
        "acceptance": [{"text": "a"}]})
    assert response.status == 400
    assert response.json()["error"]["details"]["rejected_fields"] == ["request_id"]


@pytest.mark.parametrize("field", ["actor", "author", "submitted_by", "decided_by"])
def test_identity_fields_in_a_body_are_rejected(operator, field):
    """Frozen decision 1: the actor is bound from the credential.

    All four names are refused, not just `actor` -- the rule is that identity
    never arrives in a body, and a client that tries `author` instead has made
    the same mistake.
    """
    response = operator.post("/tickets", {
        "request_id": rid(), "title": "t", "outcome": "o",
        "acceptance": [{"text": "a"}], field: {"type": "human", "id": "mem_x"}})
    assert response.status == 400
    payload = response.json()["error"]
    assert payload["code"] == "malformed_request"
    assert payload["details"]["rejected_fields"] == [field]
    # The published message, not the generic unknown-field one. These fields
    # would be refused either way -- they are not in any route's allow-list --
    # so without this assertion the explicit check could be deleted and every
    # test would still pass. It earns its keep by saying *why*.
    assert payload["message"] == (
        "Request body contained an 'actor' field. "
        "Actor is bound from the credential.")


def test_an_unknown_field_is_rejected(operator):
    """`additionalProperties: false` is the contract, so it has to be the server."""
    response = operator.post("/tickets", {
        "request_id": rid(), "title": "t", "outcome": "o",
        "acceptance": [{"text": "a"}], "priority": "high"})
    assert response.status == 400
    assert response.json()["error"]["details"]["rejected_fields"] == ["priority"]


def test_missing_required_fields_are_named(operator):
    response = operator.post("/tickets", {"request_id": rid(), "title": "t"})
    assert response.status == 400
    assert response.json()["error"]["details"]["missing_fields"] == \
        ["acceptance", "outcome"]


def test_a_ticket_needs_at_least_one_acceptance_criterion(operator):
    response = operator.post("/tickets", {
        "request_id": rid(), "title": "t", "outcome": "o", "acceptance": []})
    assert response.status == 400


def test_a_boolean_is_not_an_integer_version(operator, ticket):
    """`expected_version: true` must not compare equal to version 1."""
    response = operator.post("/tickets/{}/blocked".format(ticket["id"]), {
        "request_id": rid(), "expected_version": True, "blocked": True,
        "reason": "r"})
    assert response.status == 400
    assert response.json()["error"]["details"]["rejected_fields"] == \
        ["expected_version"]


def test_a_body_that_is_not_json_is_400(operator, server, project):
    from ticket_board.server.wire import Request

    response = server.handle(Request(
        "POST", "/tickets", headers=operator.headers(), body=b"{not json"))
    assert response.status == 400


def test_blocking_without_a_reason_is_refused(operator, ticket):
    response = operator.post("/tickets/{}/blocked".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "blocked": True})
    assert response.status == 400
    assert response.json()["error"]["details"]["missing_fields"] == ["reason"]


def test_an_update_without_a_next_step_is_refused(enrolled, ticket):
    """An update without an explicit next step is not progress. Contract, not style."""
    response = enrolled["client"].post("/tickets/{}/updates".format(ticket["id"]), {
        "request_id": rid(), "body": "did some things"})
    assert response.status == 400
    assert response.json()["error"]["details"]["missing_fields"] == ["next_step"]


def test_the_error_echoes_the_request_id_when_it_is_a_uuid(operator, ticket):
    key = rid()
    response = operator.post("/tickets/{}/blocked".format(ticket["id"]), {
        "request_id": key, "expected_version": 999, "blocked": True,
        "reason": "r"})
    assert response.status == 409
    assert response.json()["error"]["request_id"] == key


def test_the_error_omits_a_request_id_it_cannot_validate(operator):
    """`ErrorResponse.request_id` is a RequestId; echoing junk would break it."""
    response = operator.get("/tickets/DEMO-999",
                            headers={"X-Request-Id": "<script>"})
    assert response.status == 404
    assert "request_id" not in response.json()["error"]
