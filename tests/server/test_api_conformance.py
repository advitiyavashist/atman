"""Every response this server sends validates against the frozen contract.

The bar T-178 set is `additionalProperties: false` on every schema, so this
catches the two failure modes the storage lane already hit once: a field the
contract does not have, and a `null` where the contract wants the key omitted.

`test_every_declared_error_code_that_this_lane_can_raise_renders` and
`test_a_planted_extra_field_is_caught` exist so a green bar here is evidence
rather than decoration -- if the validator were mis-wired, they would pass
anyway and this file would prove nothing.
"""

import json
import uuid

import pytest

from api_client import Client, rid
from api_spec import check, is_valid

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
OTHER_SHA = "b2c3d4e5f60718293a4b5c6d7e8f90123456789a"


def evidence(sha=SHA, status="passed"):
    return {"repository": "advitiyavashist/tickets", "branch": "feature/demo-13",
            "sha": sha, "pr_url": None,
            "checks": [{"name": "pytest", "status": status, "details_url": None}]}


def test_read_screens_validate(operator, ticket, enrolled):
    check("OverviewResponse", operator.get("/overview").json(), label="GET /overview")
    check("TicketListResponse", operator.get("/tickets").json(), label="GET /tickets")
    check("TicketDetailResponse",
          operator.get("/tickets/" + ticket["id"]).json(),
          label="GET /tickets/{id}")
    check("AgentListResponse", operator.get("/agents").json(), label="GET /agents")
    check("ActivityResponse", operator.get("/activity").json(), label="GET /activity")
    check("MasterPanelResponse", operator.get("/master").json(), label="GET /master")


def test_empty_screens_validate_and_carry_empty_state(operator):
    for path, schema in (("/overview", "OverviewResponse"),
                         ("/tickets", "TicketListResponse"),
                         ("/agents", "AgentListResponse")):
        payload = check(schema, operator.get(path).json(), label="empty " + path)
        assert "empty_state" in payload, path
        # Server-supplied copy: the console renders this rather than inventing
        # its own wording, so it has to be present and non-empty.
        assert payload["empty_state"]["headline"]


def test_activity_empty_state_needs_a_filter_to_reach(operator):
    """A brand-new board already has activity: creating the project audits.

    So the activity screen's empty state is only reachable through a filter that
    matches nothing. Worth an explicit test rather than a missing one -- the
    console lane will otherwise wait for a state that never arrives on an
    unfiltered feed.
    """
    unfiltered = check("ActivityResponse", operator.get("/activity").json())
    assert unfiltered["items"] and "empty_state" not in unfiltered

    filtered = check("ActivityResponse",
                     operator.get("/activity", query="subject_type=channel").json())
    assert filtered["items"] == []
    assert filtered["empty_state"]["headline"] == "No activity yet."


def test_screens_with_content_omit_empty_state(operator, ticket):
    assert "empty_state" not in operator.get("/tickets").json()
    assert "empty_state" not in operator.get("/overview").json()


def test_ticket_mutations_validate(operator, enrolled, ticket):
    agent = enrolled["client"]
    claimed = agent.post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"],
    })
    check("Ticket", claimed.json(), label="POST claim")

    update = agent.post("/tickets/{}/updates".format(ticket["id"]), {
        "request_id": rid(), "body": "Cursor decoder drafted.",
        "next_step": "Fix the off-by-one.", "session_id": enrolled["session_id"],
    })
    check("TicketUpdate", update.json(), label="POST updates")

    # The update bumped the ticket, so re-read: `expected_version` is the
    # client's job to carry and a stale one is a 409, which is the point.
    version = operator.get("/tickets/" + ticket["id"]).json()["ticket"]["version"]
    review = agent.post("/tickets/{}/reviews".format(ticket["id"]), {
        "request_id": rid(), "expected_version": version,
        "evidence": evidence(), "notes": "Covered by tests.",
    })
    check("Review", review.json(), label="POST reviews")

    detail = operator.get("/tickets/" + ticket["id"]).json()
    decided = operator.post("/tickets/{}/reviews/{}/decision".format(
        ticket["id"], review.json()["id"]), {
        "request_id": rid(), "expected_version": detail["ticket"]["version"],
        "decision": "accept", "evidence_sha": SHA, "notes": "Matches the SHA.",
    })
    check("Review", decided.json(), label="POST decision")
    check("TicketDetailResponse", operator.get("/tickets/" + ticket["id"]).json(),
          label="detail after accept")


def test_blocked_toggle_validates(operator, ticket):
    blocked = operator.post("/tickets/{}/blocked".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "blocked": True, "reason": "Waiting on staging credentials.",
    })
    check("Ticket", blocked.json(), label="POST blocked")
    assert blocked.json()["state"] == "blocked"
    unblocked = operator.post("/tickets/{}/blocked".format(ticket["id"]), {
        "request_id": rid(), "expected_version": blocked.json()["version"],
        "blocked": False,
    })
    check("Ticket", unblocked.json(), label="POST unblocked")
    assert unblocked.json()["state"] == "open"


def test_enrollment_and_session_validate(server, project, operator):
    created = operator.post("/enrollments", {
        "request_id": rid(), "agent_name": "backend-2", "role": "backend",
        "capabilities": ["python", "pytest"],
        "worktree": "/repos/demo/.worktrees/backend-2",
        "connection_mode": "managed", "max_active_tickets": 1,
    })
    payload = check("CreateEnrollmentResponse", created.json(),
                    label="POST /enrollments")
    session_id = "ses_" + uuid.uuid4().hex[:8]
    exchanged = Client(server, project_id=project["id"]).post("/sessions", {
        "request_id": rid(), "code": payload["code"], "session_id": session_id,
        "runtime": {"adapter": "claude_code", "version": "2.1.0"},
    })
    check("SessionCredentialResponse", exchanged.json(), label="POST /sessions")


def test_hook_event_and_agent_revocation_validate(operator, enrolled):
    posted = enrolled["client"].post("/hook-events", {
        "request_id": rid(),
        "event": {"event_id": "hev_" + uuid.uuid4().hex, "kind": "session_start",
                  "agent_id": enrolled["agent_id"],
                  "session_id": enrolled["session_id"],
                  "occurred_at": "2026-09-06T14:32:00Z",
                  "cwd": "/repos/demo", "note": None},
    })
    check("HookEventResponse", posted.json(), label="POST /hook-events")

    agents = operator.get("/agents").json()["items"]
    version = next(a for a in agents if a["id"] == enrolled["agent_id"])["version"]
    revoked = operator.delete(
        "/agents/{}/session-lease".format(enrolled["agent_id"]),
        {"request_id": rid(), "expected_version": version,
         "note": "Session unreachable for 12m. Work preserved."})
    check("Agent", revoked.json(), label="DELETE session-lease")


def test_master_routes_validate(operator, ticket, enrolled):
    lease = operator.post("/master/lease", {"request_id": rid(),
                                            "expected_epoch": 0,
                                            "ttl_seconds": 120})
    check("MasterLease", lease.json(), label="POST /master/lease")
    paused = operator.post("/master/pause", {
        "request_id": rid(), "lease_epoch": lease.json()["epoch"], "paused": True})
    check("MasterLease", paused.json(), label="POST /master/pause")
    assignment = operator.post("/assignments", {
        "request_id": rid(), "ticket_id": ticket["id"],
        "agent_id": enrolled["agent_id"],
        "reason": "Only free agent with the backend capability.",
        "lease_epoch": lease.json()["epoch"],
        "expected_version": ticket["version"], "ttl_seconds": 600,
    })
    check("Assignment", assignment.json(), label="POST /assignments")
    check("MasterPanelResponse", operator.get("/master").json(),
          label="GET /master with a queue")


# ------------------------------------------------------------------- errors

def test_every_error_this_lane_raises_renders_as_the_contract_shape(
        server, project, operator, anonymous, enrolled, ticket):
    """One assertion per error the routes can actually produce.

    Codes belonging to other lanes (`not_channel_member`, `budget_exceeded`,
    `run_already_active`, ...) are deliberately absent: this build cannot raise
    them, and asserting on a code no route emits would be theatre.
    """
    agent = enrolled["client"]
    cases = {}

    cases["unauthenticated"] = Client(
        server, project_id=project["id"]).get("/overview")
    cases["forbidden_scope"] = operator.with_(
        project_id="prj_someother1").get("/overview")
    cases["agent_token_insufficient"] = agent.post(
        "/enrollments", {"request_id": rid(), "agent_name": "x", "role": "y"})
    cases["not_found"] = operator.get("/tickets/DEMO-999")
    cases["malformed_request"] = operator.post("/tickets", {
        "request_id": rid(), "title": "t", "outcome": "o",
        "acceptance": [{"text": "a"}], "actor": {"type": "human"}})

    claimed = agent.post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]})
    assert claimed.status == 200
    cases["ticket_version_conflict"] = agent.post(
        "/tickets/{}/claim".format(ticket["id"]),
        {"request_id": rid(), "expected_version": 1,
         "session_id": enrolled["session_id"]})
    cases["request_id_reused"] = _reused_request_id(agent, enrolled, ticket)
    cases["master_lease_conflict"] = operator.post(
        "/master/lease", {"request_id": rid(), "expected_epoch": 7})
    cases["master_lease_expired"] = operator.post(
        "/master/pause", {"request_id": rid(), "lease_epoch": 9, "paused": True})
    cases["enrollment_code_invalid"] = Client(
        server, project_id=project["id"]).post("/sessions", {
            "request_id": rid(), "code": "nope", "session_id": "ses_abcdef12"})

    for code, response in cases.items():
        payload = response.json()
        check("ErrorResponse", payload, label="error " + code)
        assert payload["error"]["code"] == code, (code, payload)
        assert payload["error"]["status"] == response.status, (code, payload)


def _reused_request_id(agent, enrolled, ticket):
    key = rid()
    first = agent.post("/tickets/{}/updates".format(ticket["id"]), {
        "request_id": key, "body": "one", "next_step": "next",
        "session_id": enrolled["session_id"]})
    assert first.status == 201
    return agent.post("/tickets/{}/updates".format(ticket["id"]), {
        "request_id": key, "body": "different body", "next_step": "next",
        "session_id": enrolled["session_id"]})


def test_error_status_matches_the_declared_family(operator):
    response = operator.get("/tickets/DEMO-999")
    assert response.status == 404
    assert response.json()["error"]["status"] == 404


# ------------------------------------------- the validator is really checking

def test_a_planted_extra_field_is_caught(operator, ticket):
    """The bar bites. Without this the green bar above proves nothing."""
    payload = operator.get("/tickets/" + ticket["id"]).json()
    assert is_valid("TicketDetailResponse", payload)
    payload["ticket"]["surprise"] = "extra"
    assert not is_valid("TicketDetailResponse", payload)


def test_a_planted_null_where_the_contract_wants_omission_is_caught(operator,
                                                                   ticket):
    """The exact drift T-179 hit: `outcome: null` instead of no key at all."""
    payload = operator.get("/tickets").json()
    assert is_valid("TicketListResponse", payload)
    payload["items"][0]["role"] = None
    assert not is_valid("TicketListResponse", payload)


def test_omitted_optionals_are_omitted_not_nulled(operator):
    """A ticket created without a role must not carry `role: null`."""
    created = operator.post("/tickets", {
        "request_id": rid(), "title": "No role", "outcome": "Done when done.",
        "acceptance": [{"text": "works"}],
    })
    body = check("Ticket", created.json(), label="ticket without a role")
    assert "role" not in body
    # ... while the nullable ones are present and null.
    assert body["owner"] is None and body["blocked_reason"] is None
