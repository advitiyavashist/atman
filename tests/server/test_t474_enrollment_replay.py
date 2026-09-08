"""T-474: POST /enrollments request_id replay is WONT-amend.

The frozen CreateEnrollmentResponse says the enrollment `code` is "returned
exactly once here and never again". Storing the 201 and replaying it would
return that secret a second time. Today's handler therefore does not join the
generic request_id replay table: a byte-identical retry is a 400 naming the
duplicate agent_name, with no `code` in the body.

T-192 asked for idempotent create. That would be an OpenAPI amendment (option
(a) on this ticket). This file pins option (b): keep the freeze, keep fail-as-
duplicate-name. tests/runners/test_t192_agent_create_presets.py already pins
the name-collision ordering against the worktree registry; this file pins the
contract consequence the T-192 test deliberately refused to claim.
"""
from api_client import rid


def _enroll(operator, request_id, name="t474-replay"):
    return operator.post("/enrollments", {
        "request_id": request_id,
        "agent_name": name,
        "role": "backend",
    })


def test_identical_request_id_replay_fails_as_duplicate_name_with_no_code(
        operator, server):
    shared = rid()
    first = _enroll(operator, shared)
    assert first.status == 201, first.json()
    code = first.json()["code"]
    assert code

    second = _enroll(operator, shared)
    payload = second.json()
    assert second.status == 400, payload
    err = payload["error"]
    assert err["status"] == 400
    assert err["code"] == "malformed_request"
    assert "already exists" in err["message"]
    assert err.get("details", {}).get("rejected_fields") == ["agent_name"]
    assert "code" not in payload
    assert code not in str(payload)
    assert err["code"] != "request_id_reused"

    names = [row["name"] for row in operator.get("/agents").json()["items"]]
    assert names.count("t474-replay") == 1, names


def test_enrollment_request_id_is_not_stored_for_generic_replay(operator, server):
    """If enrollments joined _remember/_replay, a retry would 201 the secret."""
    shared = rid()
    first = _enroll(operator, shared, name="t474-noreplay")
    assert first.status == 201, first.json()
    rows = server.store.conn.execute(
        "SELECT operation FROM request_log WHERE request_id = ?",
        (shared,),
    ).fetchall()
    assert [r["operation"] for r in rows] == []
