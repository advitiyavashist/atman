"""request_id idempotency, superseded updates, and the append-only audit."""

import pytest
import sqlite3

from ticket_board.storage import MalformedRequest, RequestIdReused

RID_A = "11111111-2222-3333-4444-555555555555"
RID_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_replaying_the_same_request_returns_the_original_result(store, project, agent):
    ticket = store.create_ticket(project["id"], "IDEM-1", "Once")
    first = store.claim_ticket(project["id"], "IDEM-1", agent["id"],
                               expected_version=ticket["version"],
                               request_id=RID_A)
    second = store.claim_ticket(project["id"], "IDEM-1", agent["id"],
                                expected_version=ticket["version"],
                                request_id=RID_A)
    assert first == second, "replay must return the original result"
    # And must not have applied twice.
    assert store.get_ticket(project["id"], "IDEM-1")["version"] == \
        ticket["version"] + 1
    claims = [e for e in store.audit_trail(project["id"])
              if e["action"] == "ticket.claim"]
    assert len(claims) == 1, "a replay must not append a second audit event"


def test_same_request_id_with_a_different_body_is_409(store, project):
    a = store.create_agent(project["id"], "one")
    b = store.create_agent(project["id"], "two")
    ticket = store.create_ticket(project["id"], "IDEM-2", "Once")
    store.claim_ticket(project["id"], "IDEM-2", a["id"],
                       expected_version=ticket["version"], request_id=RID_B)
    with pytest.raises(RequestIdReused) as caught:
        store.claim_ticket(project["id"], "IDEM-2", b["id"],
                           expected_version=ticket["version"], request_id=RID_B)
    assert caught.value.status == 409
    assert caught.value.details["request_id"] == RID_B


def test_request_id_must_be_a_uuid(store, project):
    store.create_ticket(project["id"], "IDEM-3", "t")
    t = store.get_ticket(project["id"], "IDEM-3")
    with pytest.raises(MalformedRequest):
        store.set_dependencies(project["id"], "IDEM-3", [],
                               expected_version=t["version"],
                               request_id="not-a-uuid")


def test_idempotency_is_scoped_per_project(store):
    """The same request_id in two projects is two different requests."""
    p1 = store.create_project("P1")
    p2 = store.create_project("P2")
    store.create_ticket(p1["id"], "SCOPE-1", "a", request_id=RID_A)
    # Must not replay p1's response into p2.
    made = store.create_ticket(p2["id"], "SCOPE-1", "b", request_id=RID_A)
    assert made["project_id"] == p2["id"]
    assert made["title"] == "b"


def test_late_update_from_a_superseded_session_is_kept_not_dropped(store, project):
    """The displaced session's account is the most useful thing after a takeover."""
    a = store.create_agent(project["id"], "first")
    b = store.create_agent(project["id"], "second")
    session_a = store.open_session(a["id"], "2099-01-01T00:00:00Z")
    ticket = store.create_ticket(project["id"], "LATE-1", "Handover")
    claimed = store.claim_ticket(project["id"], "LATE-1", a["id"],
                                 expected_version=ticket["version"],
                                 session_id=session_a["session_id"])

    # Ticket is taken over by b.
    store.transition(project["id"], "LATE-1", "open",
                     expected_version=claimed["version"])
    reopened = store.get_ticket(project["id"], "LATE-1")
    session_b = store.open_session(b["id"], "2099-01-01T00:00:00Z")
    store.claim_ticket(project["id"], "LATE-1", b["id"],
                       expected_version=reopened["version"],
                       session_id=session_b["session_id"])
    before = store.get_ticket(project["id"], "LATE-1")

    late = store.add_update(
        project["id"], "LATE-1",
        {"type": "agent", "id": a["id"], "display_name": "first"},
        "I was halfway through the migration when I lost the ticket.",
        session_id=session_a["session_id"],
    )

    assert late["superseded"] is True, "must be flagged"
    stored = store.list_updates(project["id"], "LATE-1")
    assert any(u["id"] == late["id"] for u in stored), "must be KEPT, not dropped"

    after = store.get_ticket(project["id"], "LATE-1")
    assert after["owner"] == b["id"], "a superseded update must not change ownership"
    assert after["last_progress_at"] == before["last_progress_at"], \
        "a superseded update must not count as progress"
    assert after["version"] == before["version"], "and must not bump the version"


def test_current_session_update_does_count_as_progress(store, project, agent):
    session = store.open_session(agent["id"], "2099-01-01T00:00:00Z")
    ticket = store.create_ticket(project["id"], "PROG-1", "Working")
    store.claim_ticket(project["id"], "PROG-1", agent["id"],
                       expected_version=ticket["version"],
                       session_id=session["session_id"])
    update = store.add_update(
        project["id"], "PROG-1",
        {"type": "agent", "id": agent["id"], "display_name": "backend-1"},
        "Wired the decoder.", next_step="Add tests",
        session_id=session["session_id"],
    )
    assert update["superseded"] is False
    assert store.get_ticket(project["id"], "PROG-1")["next_step"] == "Add tests"


def test_revoked_session_update_is_superseded(store, project, agent):
    session = store.open_session(agent["id"], "2099-01-01T00:00:00Z")
    ticket = store.create_ticket(project["id"], "REV-1", "Working")
    store.claim_ticket(project["id"], "REV-1", agent["id"],
                       expected_version=ticket["version"],
                       session_id=session["session_id"])
    store.revoke_session(session["session_id"], note="operator revoked")
    late = store.add_update(
        project["id"], "REV-1",
        {"type": "agent", "id": agent["id"], "display_name": "backend-1"},
        "still going", session_id=session["session_id"],
    )
    assert late["superseded"] is True


def test_omitted_session_id_is_unattributed_and_denied_effect(store, project, agent):
    """session_id is optional on the wire; absent must not read as clean."""
    session = store.open_session(agent["id"], "2099-01-01T00:00:00Z")
    ticket = store.create_ticket(project["id"], "UNATTR-1", "Working")
    store.claim_ticket(project["id"], "UNATTR-1", agent["id"],
                       expected_version=ticket["version"],
                       session_id=session["session_id"])
    before = store.get_ticket(project["id"], "UNATTR-1")

    update = store.add_update(
        project["id"], "UNATTR-1",
        {"type": "agent", "id": agent["id"], "display_name": "backend-1"},
        "still going", next_step="should not land",
    )  # no session_id at all

    assert update["superseded"] is False, \
        "the wire has no third value for this field"
    after = store.get_ticket(project["id"], "UNATTR-1")
    assert after["last_progress_at"] == before["last_progress_at"], \
        "an unattributed update must not count as progress"
    assert after["next_step"] == before["next_step"], \
        "an unattributed update must not overwrite next_step"


def test_a_displaced_agent_omitting_session_id_does_not_read_as_healthy(store, project):
    """The exact case from the T-225/T-239 report, both ways at once: same
    displaced agent, same dead session -- WITH session_id must be superseded,
    WITHOUT must not silently pass as an ordinary progress update either."""
    a = store.create_agent(project["id"], "first")
    b = store.create_agent(project["id"], "second")
    session_a = store.open_session(a["id"], "2099-01-01T00:00:00Z")
    ticket = store.create_ticket(project["id"], "DISP-1", "Handover")
    claimed = store.claim_ticket(project["id"], "DISP-1", a["id"],
                                 expected_version=ticket["version"],
                                 session_id=session_a["session_id"])
    store.transition(project["id"], "DISP-1", "open",
                     expected_version=claimed["version"])
    reopened = store.get_ticket(project["id"], "DISP-1")
    session_b = store.open_session(b["id"], "2099-01-01T00:00:00Z")
    store.claim_ticket(project["id"], "DISP-1", b["id"],
                       expected_version=reopened["version"],
                       session_id=session_b["session_id"])
    before = store.get_ticket(project["id"], "DISP-1")

    with_session = store.add_update(
        project["id"], "DISP-1",
        {"type": "agent", "id": a["id"], "display_name": "first"},
        "with session_id", session_id=session_a["session_id"],
    )
    without_session = store.add_update(
        project["id"], "DISP-1",
        {"type": "agent", "id": a["id"], "display_name": "first"},
        "without session_id",
    )

    assert with_session["superseded"] is True
    assert without_session["superseded"] is False, "the boolean has no third state"
    after = store.get_ticket(project["id"], "DISP-1")
    assert after["last_progress_at"] == before["last_progress_at"]
    assert after["owner"] == b["id"]

    trail = [e for e in store.audit_trail(project["id"])
             if e["subject_id"] == "DISP-1" and e["action"].startswith("ticket.update")]
    assert [e["action"] for e in trail] == [
        "ticket.update.superseded", "ticket.update.unattributed",
    ], "must never collapse into an ordinary ticket.update"


def test_valid_superseded_and_unattributed_audit_actions_are_never_collapsed(
        store, project, agent):
    """The three session states each need their own audit action so a reader
    can tell them apart without re-deriving them from raw session state."""
    session = store.open_session(agent["id"], "2099-01-01T00:00:00Z")
    ticket = store.create_ticket(project["id"], "AXIS-1", "Working")
    store.claim_ticket(project["id"], "AXIS-1", agent["id"],
                       expected_version=ticket["version"],
                       session_id=session["session_id"])

    store.add_update(project["id"], "AXIS-1",
                     {"type": "agent", "id": agent["id"], "display_name": "b"},
                     "clean", session_id=session["session_id"])
    store.revoke_session(session["session_id"], note="operator revoked")
    store.add_update(project["id"], "AXIS-1",
                     {"type": "agent", "id": agent["id"], "display_name": "b"},
                     "stale", session_id=session["session_id"])
    store.add_update(project["id"], "AXIS-1",
                     {"type": "agent", "id": agent["id"], "display_name": "b"},
                     "unattributed")

    actions = [e["action"] for e in store.audit_trail(project["id"])
              if e["subject_id"] == "AXIS-1" and e["action"].startswith("ticket.update")]
    assert actions == [
        "ticket.update", "ticket.update.superseded", "ticket.update.unattributed",
    ]


def test_heartbeat_does_not_touch_progress(store, project, agent):
    """A live-but-stalled agent must stay visible. Two fields, two meanings."""
    session = store.open_session(agent["id"], "2099-01-01T00:00:00Z")
    ticket = store.create_ticket(project["id"], "HB-1", "Working")
    store.claim_ticket(project["id"], "HB-1", agent["id"],
                       expected_version=ticket["version"],
                       session_id=session["session_id"])
    store.add_update(project["id"], "HB-1",
                     {"type": "agent", "id": agent["id"], "display_name": "b"},
                     "real progress", session_id=session["session_id"])
    progress_at = store.get_agent(agent["id"])["last_progress_at"]

    store.record_heartbeat(agent["id"], at="2099-06-06T00:00:00Z")
    live = store.get_agent(agent["id"])
    assert live["last_heartbeat_at"] == "2099-06-06T00:00:00Z"
    assert live["last_progress_at"] == progress_at, \
        "a heartbeat must never be mistaken for progress"


def test_audit_events_cannot_be_updated_or_deleted(store, project):
    """Append-only enforced by the database, not by convention."""
    store.create_ticket(project["id"], "AUD-1", "t")
    trail = store.audit_trail(project["id"])
    assert trail, "creating a ticket must be audited"

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.conn.execute("UPDATE audit_events SET action = 'tampered'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.conn.execute("DELETE FROM audit_events")

    assert store.audit_trail(project["id"]) == trail


def test_audit_sequence_is_monotonic_and_event_ids_sort(store, project):
    """`Last-Event-ID` reconnect depends on lexicographic order matching seq."""
    for n in range(1, 6):
        store.create_ticket(project["id"], "SEQ-{}".format(n), "t{}".format(n))
    trail = store.audit_trail(project["id"])
    event_ids = [e["event_id"] for e in trail]
    assert event_ids == sorted(event_ids), "stream ids must sort lexicographically"


def test_audit_trail_pages_from_a_cursor(store, project):
    for n in range(1, 6):
        store.create_ticket(project["id"], "PAGE-{}".format(n), "t")
    everything = store.audit_trail(project["id"])
    tail = store.audit_trail(project["id"], after_seq=2)
    assert len(tail) == len(everything) - 2
