"""Screens: counts, the attention queue, paging, and the stream banner."""

import json

import pytest

from api_client import Client, rid
from api_spec import check
from ticket_board.server.auth import in_seconds
from ticket_board.server.views import encode_cursor

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


def make(operator, title, **extra):
    body = {"request_id": rid(), "title": title, "outcome": "o",
            "acceptance": [{"text": "a"}]}
    body.update(extra)
    response = operator.post("/tickets", body)
    assert response.status == 201, response.json()
    return response.json()


# ------------------------------------------------------------------ counts

def test_counts_separate_ready_from_dependency_blocked(operator, enrolled):
    ready = make(operator, "Ready")
    blocker = make(operator, "Blocker")
    make(operator, "Dependent", dependencies=[blocker["id"]])
    claimed = make(operator, "Claimed")
    enrolled["client"].post("/tickets/{}/claim".format(claimed["id"]), {
        "request_id": rid(), "expected_version": claimed["version"],
        "session_id": enrolled["session_id"]})
    blocked = make(operator, "Blocked")
    operator.post("/tickets/{}/blocked".format(blocked["id"]), {
        "request_id": rid(), "expected_version": blocked["version"],
        "blocked": True, "reason": "Waiting on credentials."})

    counts = operator.get("/overview").json()["counts"]
    assert counts == {"ready": 2, "in_progress": 1, "awaiting_review": 0,
                      "blocked": 1, "dependency_blocked": 1}


def test_a_dependency_blocked_ticket_is_open_not_blocked(operator):
    blocker = make(operator, "Blocker")
    dependent = make(operator, "Dependent", dependencies=[blocker["id"]])
    assert dependent["state"] == "open"
    assert dependent["dependency_blocked"] is True
    assert operator.get("/overview").json()["counts"]["blocked"] == 0


# --------------------------------------------------------- attention queue

def test_a_ticket_awaiting_review_raises_an_attention_item(operator, enrolled,
                                                            ticket):
    claimed = enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]}).json()
    enrolled["client"].post("/tickets/{}/reviews".format(ticket["id"]), {
        "request_id": rid(), "expected_version": claimed["version"],
        "evidence": {"branch": "b", "sha": SHA}})
    attention = operator.get("/overview").json()["attention"]
    item = next(i for i in attention if i["kind"] == "awaiting_review")
    assert item["subject_type"] == "ticket" and item["subject_id"] == ticket["id"]
    assert item["acknowledged_by"] is None


def test_a_blocked_ticket_carries_its_reason_into_the_queue(operator, ticket):
    operator.post("/tickets/{}/blocked".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "blocked": True, "reason": "Waiting on staging credentials."})
    item = next(i for i in operator.get("/overview").json()["attention"]
                if i["kind"] == "blocked")
    assert "staging credentials" in item["headline"]


def test_a_silent_agent_raises_no_heartbeat(server, operator, enrolled):
    server.store.conn.execute(
        "UPDATE agents SET last_heartbeat_at = ? WHERE id = ?",
        (in_seconds(-600), enrolled["agent_id"]))
    item = next(i for i in operator.get("/overview").json()["attention"]
                if i["kind"] == "no_heartbeat")
    assert item["subject_id"] == enrolled["agent_id"]
    assert "Check session" in item["headline"]


def test_a_live_but_stalled_agent_is_visible_separately(server, operator,
                                                         enrolled, ticket):
    """The reason heartbeat and progress are two fields.

    Collapsing them would hide exactly this agent: it is answering, so it looks
    healthy, and it has not moved in twenty minutes.
    """
    enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]})
    server.store.conn.execute(
        "UPDATE agents SET last_heartbeat_at = ?, last_progress_at = ?"
        " WHERE id = ?",
        (in_seconds(-10), in_seconds(-1200), enrolled["agent_id"]))
    kinds = {i["kind"] for i in operator.get("/overview").json()["attention"]}
    assert "progress_stalled" in kinds
    assert "no_heartbeat" not in kinds
    # And the agent still reads as working, not offline.
    agent = next(a for a in operator.get("/agents").json()["items"]
                 if a["id"] == enrolled["agent_id"])
    assert agent["state"] == "working"


def test_a_hook_delivery_failure_is_surfaced_and_marks_the_agent_offline(
        server, operator, enrolled):
    health = {"config_installed": True, "server_received": True,
              "response_delivered": True, "session_adopted": True,
              "last_error": "Connection refused to 127.0.0.1:4319"}
    server.store.conn.execute("UPDATE agents SET hook_health = ? WHERE id = ?",
                              (json.dumps(health), enrolled["agent_id"]))
    overview = operator.get("/overview").json()
    assert any(i["kind"] == "hook_delivery_failed" for i in overview["attention"])
    agent = next(a for a in operator.get("/agents").json()["items"]
                 if a["id"] == enrolled["agent_id"])
    assert agent["state"] == "offline"


def test_an_expired_master_lease_is_surfaced(server, project, operator):
    """Nobody is routing work, which is the operator's problem to see.

    Worth stating what this alert cannot be keyed on: an expired lease reports
    `holder: null`, so "there is a holder and it expired" is a condition that
    never occurs. The first version of this check was written that way and could
    not fire.
    """
    lease = operator.post("/master/lease", {"request_id": rid(),
                                            "expected_epoch": 0}).json()
    server.store.conn.execute(
        "UPDATE master_lease SET expires_at = ? WHERE project_id = ?",
        (in_seconds(-60), project["id"]))
    overview = operator.get("/overview").json()
    assert overview["master"]["holder"] is None
    item = next(i for i in overview["attention"]
                if i["kind"] == "master_lease_expired")
    assert item["subject_type"] == "master_lease"
    assert "No one is routing work." in item["headline"]

    # Taking it again clears the alert.
    operator.post("/master/lease", {"request_id": rid(),
                                    "expected_epoch": lease["epoch"]})
    assert not [i for i in operator.get("/overview").json()["attention"]
                if i["kind"] == "master_lease_expired"]


def test_a_board_that_never_had_a_master_raises_no_lease_alert(operator, ticket):
    """Epoch 0 means nobody has ever routed here, which is not a fault."""
    kinds = {i["kind"] for i in operator.get("/overview").json()["attention"]}
    assert "master_lease_expired" not in kinds


def test_recent_accepted_shows_the_decided_review(operator, enrolled, ticket,
                                                   server, project):
    claimed = enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]}).json()
    review = enrolled["client"].post("/tickets/{}/reviews".format(ticket["id"]), {
        "request_id": rid(), "expected_version": claimed["version"],
        "evidence": {"branch": "b", "sha": SHA}}).json()
    version = operator.get("/tickets/" + ticket["id"]).json()["ticket"]["version"]
    operator.post("/tickets/{}/reviews/{}/decision".format(ticket["id"],
                                                           review["id"]),
                  {"request_id": rid(), "expected_version": version,
                   "decision": "accept", "evidence_sha": SHA})
    accepted = operator.get("/overview").json()["recent_accepted"]
    assert [r["id"] for r in accepted] == [review["id"]]


# ------------------------------------------------------------------ paging

def test_the_ticket_list_pages_and_reports_the_full_match_count(operator):
    for index in range(5):
        make(operator, "T{}".format(index))
    first = operator.get("/tickets", query="limit=2").json()
    assert len(first["items"]) == 2
    assert first["total_matching"] == 5
    assert first["next_cursor"]

    second = operator.get("/tickets",
                          query="limit=2&cursor=" + first["next_cursor"]).json()
    assert len(second["items"]) == 2
    ids = [t["id"] for t in first["items"] + second["items"]]
    assert len(set(ids)) == 4, "a page boundary must not repeat or skip a row"

    last = operator.get("/tickets",
                        query="limit=2&cursor=" + second["next_cursor"]).json()
    assert len(last["items"]) == 1 and last["next_cursor"] is None


def test_a_cursor_from_another_listing_is_refused(operator):
    """Opaque, but typed: replaying an activity cursor here would page wrongly."""
    response = operator.get("/tickets",
                            query="cursor=" + encode_cursor("activity", 4))
    assert response.status == 400
    assert response.json()["error"]["details"]["rejected_fields"] == ["cursor"]


def test_a_garbage_cursor_is_refused(operator):
    assert operator.get("/tickets", query="cursor=!!!!").status == 400


@pytest.mark.parametrize("limit", ["0", "201", "many"])
def test_limits_outside_the_contract_are_refused(operator, limit):
    assert operator.get("/tickets", query="limit=" + limit).status == 400


def test_filters_narrow_the_list(operator, enrolled, ticket):
    make(operator, "Frontend work", role="frontend")
    enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]})
    assert [t["id"] for t in operator.get(
        "/tickets", query="state=claimed").json()["items"]] == [ticket["id"]]
    assert [t["title"] for t in operator.get(
        "/tickets", query="role=frontend").json()["items"]] == ["Frontend work"]
    assert [t["id"] for t in operator.get(
        "/tickets", query="owner=" + enrolled["agent_id"]).json()["items"]] == \
        [ticket["id"]]
    assert [t["title"] for t in operator.get(
        "/tickets", query="q=frontend").json()["items"]] == ["Frontend work"]


def test_activity_pages_forward_without_repeating(operator):
    for index in range(6):
        make(operator, "T{}".format(index))
    first = operator.get("/activity", query="limit=3").json()
    assert len(first["items"]) == 3 and first["next_cursor"]
    second = operator.get("/activity",
                          query="limit=3&cursor=" + first["next_cursor"]).json()
    ids = [e["id"] for e in first["items"] + second["items"]]
    assert len(set(ids)) == len(ids)


def test_activity_filters_by_subject_type(operator, ticket):
    items = operator.get("/activity", query="subject_type=ticket").json()["items"]
    assert items and all(e["subject_type"] == "ticket" for e in items)


# --------------------------------------------------------- detail and stream

def test_dependencies_are_resolved_so_the_console_reads_once(operator):
    blocker = make(operator, "Blocker")
    dependent = make(operator, "Dependent", dependencies=[blocker["id"]])
    detail = operator.get("/tickets/" + dependent["id"]).json()
    assert detail["dependencies"] == [
        {"id": blocker["id"], "title": "Blocker", "state": "open"}]


def test_a_dependency_that_is_not_on_this_board_is_reported_not_dropped(operator):
    """It is the reason the dependent is blocked; silence would be worse."""
    dependent = make(operator, "Dependent", dependencies=["OTHER-1"])
    detail = operator.get("/tickets/" + dependent["id"]).json()
    assert detail["dependencies"] == [
        {"id": "OTHER-1", "title": "Not on this board", "state": "open"}]
    assert detail["ticket"]["dependency_blocked"] is True


def test_available_actions_differ_by_principal(operator, enrolled, ticket):
    operator_view = operator.get("/tickets/" + ticket["id"]).json()
    agent_view = enrolled["client"].get("/tickets/" + ticket["id"]).json()
    assert "claim" in agent_view["available_actions"]
    assert "claim" not in operator_view["available_actions"]
    assert "block" in operator_view["available_actions"]


def test_a_dependency_blocked_ticket_is_not_offered_as_claimable(operator,
                                                                  enrolled):
    blocker = make(operator, "Blocker")
    dependent = make(operator, "Dependent", dependencies=[blocker["id"]])
    view = enrolled["client"].get("/tickets/" + dependent["id"]).json()
    assert "claim" not in view["available_actions"]


def test_every_screen_carries_the_stream_banner(operator, ticket):
    for path in ("/overview", "/tickets", "/agents", "/activity", "/master",
                 "/tickets/" + ticket["id"]):
        stream = operator.get(path).json()["stream"]
        assert stream["state"] == "live"
        assert stream["snapshot_version"] >= 1
        assert stream["as_of"].endswith("Z")
        assert stream["last_event_id"].startswith("evt_")


def test_the_overview_publishes_its_snapshot_version_as_a_header(operator,
                                                                  ticket):
    response = operator.get("/overview")
    assert response.headers["X-Snapshot-Version"] == \
        str(response.json()["stream"]["snapshot_version"])


def test_the_snapshot_version_advances_with_each_mutation(operator, ticket):
    before = operator.get("/overview").json()["stream"]["snapshot_version"]
    make(operator, "Another")
    after = operator.get("/overview").json()["stream"]["snapshot_version"]
    assert after > before


def test_an_audit_row_without_a_subject_still_validates(server, project,
                                                        operator):
    """Optional plain-typed fields are omitted, never nulled.

    No route in this build writes an audit row without a subject, so this plants
    one directly: T-182's sweep and T-187's messaging both will, and
    `AuditEvent.subject_type` is an enum that `null` fails.
    """
    server.store.conn.execute(
        "INSERT INTO audit_events (id, seq, event_id, project_id, actor, action,"
        " occurred_at, summary) VALUES ('aud_planted00', 9999,"
        " 'evt_000000009999_aaaaaa', ?, '{\"type\":\"system\",\"id\":\"system\","
        "\"display_name\":\"server\"}', 'system.tick', '2026-09-06T14:35:00Z', '')",
        (project["id"],))
    payload = check("ActivityResponse", operator.get("/activity").json())
    planted = next(e for e in payload["items"] if e["id"] == "aud_planted00")
    assert "subject_type" not in planted and "subject_id" not in planted
