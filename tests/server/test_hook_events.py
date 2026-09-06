"""Hook intake: dedupe, the four health booleans, and what a hook never grants."""

import uuid

import pytest

from api_client import rid
from ticket_board.server.auth import in_seconds
from ticket_board.server.hooks import RateLimiter


def event(enrolled, kind="session_start", event_id=None, **extra):
    body = {"event_id": event_id or ("hev_" + uuid.uuid4().hex),
            "agent_id": enrolled["agent_id"],
            "session_id": enrolled["session_id"], "kind": kind,
            "occurred_at": "2026-09-06T14:32:00Z"}
    body.update(extra)
    return {"request_id": rid(), "event": body}


def post(enrolled, **kwargs):
    return enrolled["client"].post("/hook-events", event(enrolled, **kwargs))


def test_a_real_event_marks_the_session_adopted(operator, enrolled):
    assert post(enrolled).status == 200
    health = _health(operator, enrolled)
    assert health["server_received"] is True
    assert health["response_delivered"] is True
    assert health["session_adopted"] is True


def test_a_probe_never_marks_the_session_adopted(operator, enrolled):
    """A synthetic probe proves reachability, not that a session adopted the hook."""
    response = post(enrolled, kind="probe")
    assert response.status == 200
    health = _health(operator, enrolled)
    assert health["server_received"] is True
    assert health["response_delivered"] is True
    assert health["session_adopted"] is False
    assert "does not prove session adoption" in \
        " ".join(response.json()["context"]["lines"])


def test_a_retried_event_yields_one_record_and_still_answers(server, operator,
                                                             enrolled):
    reused = "hev_" + uuid.uuid4().hex
    first = post(enrolled, event_id=reused)
    second = post(enrolled, event_id=reused)
    assert first.json()["deduplicated"] is False
    assert second.json()["deduplicated"] is True
    assert second.status == 200 and second.json()["accepted"] is True
    rows = server.store.conn.execute(
        "SELECT COUNT(*) FROM hook_events WHERE event_id = ?", (reused,)).fetchone()
    assert rows[0] == 1
    audits = server.store.conn.execute(
        "SELECT COUNT(*) FROM audit_events WHERE action LIKE 'hook.%'").fetchone()
    assert audits[0] == 1


def test_a_stop_event_is_a_finished_turn_not_a_finished_ticket(operator,
                                                               enrolled, ticket):
    enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]})
    response = post(enrolled, kind="stop")
    lines = " ".join(response.json()["context"]["lines"])
    assert "Turn finished" in lines
    assert "does not complete a ticket" in lines
    assert operator.get("/tickets/" + ticket["id"]).json()["ticket"]["state"] == \
        "claimed"


def test_a_hook_event_never_grants_a_claim(operator, enrolled, ticket):
    """Adapter health and work authorization are separate systems."""
    post(enrolled)
    after = operator.get("/tickets/" + ticket["id"]).json()["ticket"]
    assert after["state"] == "open" and after["owner"] is None


def test_context_is_bounded_and_names_the_held_ticket(enrolled, ticket):
    enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]})
    enrolled["client"].post("/tickets/{}/updates".format(ticket["id"]), {
        "request_id": rid(), "body": "drafted",
        "next_step": "Fix the last page boundary.",
        "session_id": enrolled["session_id"]})
    context = post(enrolled, kind="user_prompt_submit").json()["context"]
    assert context["current_ticket"] == ticket["id"]
    assert len(context["lines"]) <= 20
    assert all(len(line) <= 300 for line in context["lines"])
    assert any("Fix the last page boundary." in line for line in context["lines"])


def test_an_event_with_an_extra_field_is_refused(enrolled):
    """The shape is bounded metadata. A new field could be a prompt."""
    body = event(enrolled)
    body["event"]["transcript"] = "the whole conversation"
    response = enrolled["client"].post("/hook-events", body)
    assert response.status == 400
    assert response.json()["error"]["details"]["rejected_fields"] == ["transcript"]


def test_an_unknown_kind_is_refused(enrolled):
    response = enrolled["client"].post("/hook-events", event(enrolled,
                                                             kind="tool_use"))
    assert response.status == 400


def test_hook_events_refresh_the_session_lease(server, enrolled, ticket):
    """An active session keeps its lease; silence lets it lapse, which is not death."""
    server.store.conn.execute(
        "UPDATE session_leases SET expires_at = ? WHERE session_id = ?",
        (in_seconds(30), enrolled["session_id"]))
    before = server.store.get_session(enrolled["session_id"])["expires_at"]
    post(enrolled)
    after = server.store.get_session(enrolled["session_id"])["expires_at"]
    assert after > before
    assert server.store.get_session(enrolled["session_id"])["revoked_at"] is None


def test_the_rate_limit_is_a_429_the_adapter_can_act_on(server, enrolled):
    server.rate_limiter = RateLimiter(limit=2, window=60)
    assert post(enrolled).status == 200
    assert post(enrolled).status == 200
    limited = post(enrolled)
    assert limited.status == 429
    payload = limited.json()["error"]
    assert payload["code"] == "rate_limited"
    assert payload["details"]["retry_after_seconds"] > 0


def test_the_rate_limit_window_resets(server, enrolled):
    limiter = RateLimiter(limit=1, window=60)
    limiter.check("agt_x", now=0)
    with pytest.raises(Exception):
        limiter.check("agt_x", now=1)
    limiter.check("agt_x", now=61)


def test_the_rate_limit_is_per_agent(server, operator, project, enrolled):
    """One noisy adapter must not lock out every other agent on the board."""
    limiter = RateLimiter(limit=1, window=60)
    limiter.check("agt_one", now=0)
    limiter.check("agt_two", now=0)


def _health(operator, enrolled):
    agents = operator.get("/agents").json()["items"]
    return next(a for a in agents if a["id"] == enrolled["agent_id"])["hook_health"]
