"""Enrollment, session credentials and lease revocation."""

import uuid

import pytest

from api_client import Client, rid
from ticket_board.server.auth import hash_secret, in_seconds


def enroll(operator, name="backend-1", **extra):
    body = {"request_id": rid(), "agent_name": name, "role": "backend"}
    body.update(extra)
    return operator.post("/enrollments", body)


def test_the_code_is_returned_once_and_never_stored(server, operator):
    created = enroll(operator)
    code = created.json()["code"]
    rows = server.store.conn.execute("SELECT code_hash FROM enrollments").fetchall()
    assert [r["code_hash"] for r in rows] == [hash_secret(code)]
    # The raw value appears nowhere in the row, so a stolen database file is
    # not a stolen enrollment.
    assert code not in "".join(str(dict(r)) for r in rows)


def test_the_code_never_appears_in_a_later_read(server, operator, project):
    created = enroll(operator)
    code = created.json()["code"]
    for path in ("/agents", "/activity", "/overview"):
        assert code not in str(operator.get(path).json())


def test_a_code_works_exactly_once(server, operator, project):
    code = enroll(operator).json()["code"]
    anon = Client(server, project_id=project["id"])
    first = anon.post("/sessions", {"request_id": rid(), "code": code,
                                    "session_id": "ses_aaaaaaaa"})
    assert first.status == 201
    second = anon.post("/sessions", {"request_id": rid(), "code": code,
                                     "session_id": "ses_bbbbbbbb"})
    assert second.status == 422
    assert second.json()["error"]["code"] == "enrollment_code_invalid"


def test_invalid_and_expired_codes_say_the_same_thing(server, operator, project):
    """Deliberately indistinguishable prose, so a code cannot be probed."""
    code = enroll(operator).json()["code"]
    server.store.conn.execute("UPDATE enrollments SET expires_at = ?",
                              (in_seconds(-60),))
    anon = Client(server, project_id=project["id"])
    expired = anon.post("/sessions", {"request_id": rid(), "code": code,
                                      "session_id": "ses_aaaaaaaa"})
    invalid = anon.post("/sessions", {"request_id": rid(), "code": "wrong",
                                      "session_id": "ses_aaaaaaaa"})
    assert expired.status == invalid.status == 422
    assert expired.json()["error"]["message"] == invalid.json()["error"]["message"]
    assert expired.json()["error"].get("details") == \
        invalid.json()["error"].get("details")
    # Neither carries details at all: a `details` block is a channel for
    # leaking which half of the guess was right.
    assert "details" not in expired.json()["error"]
    # The codes still differ, which is the freeze's shape and a documented
    # residual leak -- see docs/api-notes.md.
    assert expired.json()["error"]["code"] == "enrollment_code_expired"


def test_a_code_from_another_project_does_not_work(server, operator, project):
    other = server.store.create_project("Other")
    code = enroll(operator).json()["code"]
    response = Client(server, project_id=other["id"]).post("/sessions", {
        "request_id": rid(), "code": code, "session_id": "ses_aaaaaaaa"})
    assert response.status == 422


def test_exchange_marks_the_config_installed_and_nothing_else(server, operator,
                                                               project):
    """Running the install command is what the exchange proves. Only that."""
    code = enroll(operator).json()["code"]
    payload = Client(server, project_id=project["id"]).post("/sessions", {
        "request_id": rid(), "code": code, "session_id": "ses_aaaaaaaa",
        "runtime": {"adapter": "claude_code", "version": "2.1.0"}}).json()
    health = payload["agent"]["hook_health"]
    assert health["config_installed"] is True
    assert health["server_received"] is False
    assert health["response_delivered"] is False
    assert health["session_adopted"] is False


def test_the_token_is_returned_once_and_stored_only_as_a_hash(server, operator,
                                                              project):
    code = enroll(operator).json()["code"]
    payload = Client(server, project_id=project["id"]).post("/sessions", {
        "request_id": rid(), "code": code, "session_id": "ses_aaaaaaaa"}).json()
    rows = server.store.conn.execute("SELECT token_hash FROM agent_tokens").fetchall()
    assert [r["token_hash"] for r in rows] == [hash_secret(payload["token"])]


def test_a_duplicate_agent_name_is_refused_with_a_usable_message(operator):
    assert enroll(operator, "backend-1").status == 201
    response = enroll(operator, "backend-1")
    assert response.status == 400
    assert "already exists" in response.json()["error"]["message"]
    assert response.json()["error"]["details"]["rejected_fields"] == ["agent_name"]


def test_the_install_command_carries_no_secret(operator):
    """The code goes over stdin. A URL is logged; stdin is not."""
    created = enroll(operator).json()
    assert created["code"] not in created["install_command"]
    assert "--code" not in created["install_command"]


def test_config_changes_publish_what_will_be_written(operator):
    changes = enroll(operator).json()["config_changes"]
    assert {c["change"] for c in changes} == {"add", "none"}
    # Unrelated entries are preserved, and the response says so before the
    # adapter touches the file.
    assert any("preserved" in c["summary"] for c in changes)


# ------------------------------------------------------------ lease revocation

def test_revocation_requires_a_note(operator, enrolled):
    version = _agent_version(operator, enrolled)
    response = operator.delete(
        "/agents/{}/session-lease".format(enrolled["agent_id"]),
        {"request_id": rid(), "expected_version": version})
    assert response.status == 400
    assert response.json()["error"]["details"]["missing_fields"] == ["note"]


def test_revocation_records_the_note_and_kills_the_token(server, operator,
                                                          enrolled):
    version = _agent_version(operator, enrolled)
    note = "Session unreachable for 12m. Work preserved on feature/demo-13."
    response = operator.delete(
        "/agents/{}/session-lease".format(enrolled["agent_id"]),
        {"request_id": rid(), "expected_version": version, "note": note})
    assert response.status == 200
    assert response.json()["session"]["revocation_note"] == note
    assert response.json()["state"] == "revoked"
    # A revocation that leaves a live token behind has not stopped the session.
    assert enrolled["client"].get("/overview").status == 401


def test_revocation_preserves_the_work_and_does_not_reassign(operator, enrolled,
                                                              ticket):
    enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]})
    version = _agent_version(operator, enrolled)
    operator.delete("/agents/{}/session-lease".format(enrolled["agent_id"]),
                    {"request_id": rid(), "expected_version": version,
                     "note": "Unreachable."})
    after = operator.get("/tickets/" + ticket["id"]).json()["ticket"]
    assert after["state"] == "claimed"
    assert after["owner"] == enrolled["agent_id"]


def test_revoking_twice_is_a_409_not_a_silent_success(operator, enrolled):
    version = _agent_version(operator, enrolled)
    assert operator.delete(
        "/agents/{}/session-lease".format(enrolled["agent_id"]),
        {"request_id": rid(), "expected_version": version,
         "note": "first"}).status == 200
    version = _agent_version(operator, enrolled)
    again = operator.delete(
        "/agents/{}/session-lease".format(enrolled["agent_id"]),
        {"request_id": rid(), "expected_version": version, "note": "second"})
    assert again.status == 409
    assert again.json()["error"]["code"] == "session_lease_expired"


def test_a_stale_agent_version_is_refused(operator, enrolled):
    response = operator.delete(
        "/agents/{}/session-lease".format(enrolled["agent_id"]),
        {"request_id": rid(), "expected_version": 1, "note": "n"})
    assert response.status == 409
    details = response.json()["error"]["details"]
    # Borrowed code, honest subject: the details name the agent, not a ticket.
    assert details["agent_id"] == enrolled["agent_id"]
    assert "ticket_id" not in details


def _agent_version(operator, enrolled):
    agents = operator.get("/agents").json()["items"]
    return next(a for a in agents if a["id"] == enrolled["agent_id"])["version"]


def test_a_taken_session_id_is_refused_without_spending_the_code(
        server, operator, project):
    """The pre-check runs before redemption, so the code survives to retry.

    Ordering is the whole point of the check: if it ran after
    `consume_enrollment`, a colliding id would burn a single-use code on a
    request that could never succeed and strand the agent with no way back.
    """
    first_code = enroll(operator, name="backend-a").json()["code"]
    second_code = enroll(operator, name="backend-b").json()["code"]
    anon = Client(server, project_id=project["id"])

    taken = anon.post("/sessions", {"request_id": rid(), "code": first_code,
                                    "session_id": "ses_dupdupdup"})
    assert taken.status == 201

    collision = anon.post("/sessions", {"request_id": rid(), "code": second_code,
                                        "session_id": "ses_dupdupdup"})
    assert collision.status == 400
    assert collision.json()["error"]["code"] == "malformed_request"
    assert collision.json()["error"]["details"]["rejected_fields"] == ["session_id"]

    # The code was not spent: the same one works on a fresh session id.
    retry = anon.post("/sessions", {"request_id": rid(), "code": second_code,
                                    "session_id": "ses_freshfresh"})
    assert retry.status == 201


def test_losing_the_session_id_race_is_a_400_not_a_500(
        server, operator, project, monkeypatch):
    """Close the pre-check's TOCTOU window from the losing side.

    Two exchanges racing on one session id can both pass the SELECT before
    either INSERTs. We reproduce the loser's view by inserting the colliding
    lease *after* the check has passed -- consume_enrollment is the step that
    runs in between -- and assert the primary-key violation leaves as the same
    contract-shaped 400, not an unhandled IntegrityError as a 500.
    """
    winner_code = enroll(operator, name="backend-a").json()["code"]
    loser_code = enroll(operator, name="backend-b").json()["code"]
    anon = Client(server, project_id=project["id"])
    consume = server.credentials.consume_enrollment

    def consume_then_lose_the_race(project_id, code):
        redeemed = consume(project_id, code)
        if code == loser_code:
            anon.post("/sessions", {"request_id": rid(), "code": winner_code,
                                    "session_id": "ses_racedraced"})
        return redeemed

    monkeypatch.setattr(server.credentials, "consume_enrollment",
                        consume_then_lose_the_race)
    lost = anon.post("/sessions", {"request_id": rid(), "code": loser_code,
                                   "session_id": "ses_racedraced"})
    assert lost.status == 400
    assert lost.json()["error"]["code"] == "malformed_request"
    assert lost.json()["error"]["details"]["rejected_fields"] == ["session_id"]
    # Exactly one lease exists for the contested id, and it is the winner's.
    rows = server.store.conn.execute(
        "SELECT session_id FROM session_leases WHERE session_id = ?",
        ("ses_racedraced",)).fetchall()
    assert len(rows) == 1
