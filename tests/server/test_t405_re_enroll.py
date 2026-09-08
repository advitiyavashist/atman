"""T-405: same-identity re-enrolment for revoked agents."""

import uuid

from api_client import Client, rid


def _enroll(operator, name="backend-1", **extra):
    body = {"request_id": rid(), "agent_name": name, "role": "backend",
            "connection_mode": "managed"}
    body.update(extra)
    return operator.post("/enrollments", body)


def _revoke(operator, agent_id, version):
    return operator.delete(
        "/agents/{}/session-lease".format(agent_id),
        {"request_id": rid(), "expected_version": version, "note": "revoked"})


def _re_enroll(operator, agent_id, version, **extra):
    body = {"request_id": rid(), "expected_version": version, "role": "backend",
            "connection_mode": "managed"}
    body.update(extra)
    return operator.post("/agents/{}/enrollments".format(agent_id), body)


def test_a_revoked_agent_re_enrols_under_its_own_agent_id(server, operator, project):
    created = _enroll(operator, "recover-me", worktree="/w/recover")
    assert created.status == 201, created.json()
    original_id = created.json()["agent"]["id"]
    code = created.json()["code"]
    session_id = "ses_" + uuid.uuid4().hex[:8]
    exchanged = Client(server, project_id=project["id"]).post("/sessions", {
        "request_id": rid(), "code": code, "session_id": session_id,
        "runtime": {"adapter": "claude_code", "version": "2.1.0"},
    })
    assert exchanged.status == 201, exchanged.json()

    listed = operator.get("/agents").json()["items"]
    row = next(a for a in listed if a["id"] == original_id)
    revoked = _revoke(operator, original_id, row["version"])
    assert revoked.status == 200, revoked.json()
    assert revoked.json()["state"] == "revoked"

    re_enrolled = _re_enroll(operator, original_id, revoked.json()["version"],
                             role="reviewer", worktree="/w/recover-2")
    assert re_enrolled.status == 201, re_enrolled.json()
    assert re_enrolled.json()["agent"]["id"] == original_id
    assert re_enrolled.json()["agent"]["role"] == "reviewer"

    fresh = Client(server, project_id=project["id"]).post("/sessions", {
        "request_id": rid(), "code": re_enrolled.json()["code"],
        "session_id": "ses_" + uuid.uuid4().hex[:8],
        "runtime": {"adapter": "claude_code", "version": "2.1.0"},
    })
    assert fresh.status == 201, fresh.json()
    assert fresh.json()["agent"]["id"] == original_id
    assert fresh.json()["agent"]["state"] == "idle"
    assert fresh.json()["token"] != exchanged.json()["token"]


def test_re_enrol_preserves_ticket_ownership(server, operator, project, ticket):
    created = _enroll(operator, "ticket-owner")
    original_id = created.json()["agent"]["id"]
    code = created.json()["code"]
    session_id = "ses_" + uuid.uuid4().hex[:8]
    client = Client(server, project_id=project["id"])
    exchanged = client.post("/sessions", {
        "request_id": rid(), "code": code, "session_id": session_id,
    })
    assert exchanged.status == 201, exchanged.json()
    token = exchanged.json()["token"]
    agent_client = Client(server, project_id=project["id"], token=token)
    claimed = agent_client.post(
        "/tickets/{}/claim".format(ticket["id"]),
        {"request_id": rid(), "expected_version": ticket["version"],
         "session_id": session_id})
    assert claimed.status == 200, claimed.json()

    row = operator.get("/agents").json()["items"][0]
    revoked = _revoke(operator, original_id, row["version"])
    assert revoked.status == 200, revoked.json()

    re_enrolled = _re_enroll(operator, original_id, revoked.json()["version"])
    assert re_enrolled.status == 201, re_enrolled.json()

    after = operator.get("/tickets/{}".format(ticket["id"])).json()["ticket"]
    assert after["owner"] == original_id


def test_a_live_agent_cannot_be_re_enrolled(operator, enrolled):
    agent_id = enrolled["agent_id"]
    version = enrolled["agent"]["version"]
    response = _re_enroll(operator, agent_id, version)
    assert response.status == 400, response.json()
    assert "revoked" in response.json()["error"]["message"]


def test_re_enrol_updates_the_approved_preset(server, operator, project):
    created = _enroll(operator, "preset-agent", role="reviewer",
                      worktree="/w/preset")
    agent_id = created.json()["agent"]["id"]
    code = created.json()["code"]
    exchanged = Client(server, project_id=project["id"]).post("/sessions", {
        "request_id": rid(), "code": code,
        "session_id": "ses_" + uuid.uuid4().hex[:8],
    })
    assert exchanged.status == 201, exchanged.json()

    row = operator.get("/agents").json()["items"][0]
    revoked = _revoke(operator, agent_id, row["version"])
    assert revoked.status == 200, revoked.json()

    re_enrolled = _re_enroll(operator, agent_id, revoked.json()["version"],
                             role="worker", worktree="/w/preset-new")
    assert re_enrolled.status == 201, re_enrolled.json()

    preset = server.store.get_agent_preset(agent_id, project["id"])
    assert preset["preset"] == "worker"
    assert preset["permission_policy"] == "allowlist"
    assert preset["allowlisted_worktree"] == "/w/preset-new"


def test_re_enrol_refuses_an_occupied_worktree(server, operator, project):
    first = _enroll(operator, "occupant-a", worktree="/w/shared")
    assert first.status == 201, first.json()
    second = _enroll(operator, "occupant-b", worktree="/w/other")
    assert second.status == 201, second.json()
    code = second.json()["code"]
    exchanged = Client(server, project_id=project["id"]).post("/sessions", {
        "request_id": rid(), "code": code,
        "session_id": "ses_" + uuid.uuid4().hex[:8],
    })
    assert exchanged.status == 201, exchanged.json()

    row = next(a for a in operator.get("/agents").json()["items"]
               if a["name"] == "occupant-b")
    revoked = _revoke(operator, row["id"], row["version"])
    assert revoked.status == 200, revoked.json()

    blocked = _re_enroll(operator, row["id"], revoked.json()["version"],
                         worktree="/w/shared")
    assert blocked.status == 400, blocked.json()
    assert "occupies" in blocked.json()["error"]["message"]


def test_re_enrol_honours_expected_version(operator, enrolled):
    agent_id = enrolled["agent_id"]
    row = operator.get("/agents").json()["items"][0]
    revoked = _revoke(operator, agent_id, row["version"])
    assert revoked.status == 200, revoked.json()

    stale = _re_enroll(operator, agent_id, revoked.json()["version"] - 1)
    assert stale.status == 409, stale.json()
    assert stale.json()["error"]["code"] == "ticket_version_conflict"
