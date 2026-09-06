"""SSE frame generation: replay, retention fallback, heartbeat and the ACL filter.

These drive `EventStream.frames` directly so the assertions are about frame
content rather than socket timing; `test_reconnect_live.py` does the same thing
over a real connection.
"""

import json
import time

import pytest

from api_client import Client, rid
from api_spec import check
from ticket_board.server.events import EventStream, seq_of


def parse(chunks):
    """Split a byte stream into `(sse_id, event_type, data)` triples."""
    text = b"".join(chunks).decode("utf-8")
    frames = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block or block.startswith("retry:"):
            continue
        sse_id = kind = data = None
        for line in block.splitlines():
            if line.startswith("id: "):
                sse_id = line[4:]
            elif line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        frames.append((sse_id, kind, data))
    return frames


def collect(server, principal, project_id, *, last_event_id=None, max_frames=5,
            stream=None, timeout=0.6):
    stream = stream or server.events
    return parse(stream.frames(project_id, principal,
                               last_event_id=last_event_id,
                               max_frames=max_frames,
                               idle_deadline=time.monotonic() + timeout))


@pytest.fixture()
def principal(server, operator_session, project):
    from ticket_board.server.wire import Request

    return server.credentials.authenticate(Request(
        "GET", "/events",
        headers={"Cookie": "tb_session=" + operator_session["session_token"]}))


def test_frames_validate_as_stream_envelopes(server, principal, project,
                                             operator, ticket):
    frames = collect(server, principal, project["id"],
                     last_event_id="evt_000000000001_aaaaaa", max_frames=3)
    assert frames
    for _, _, data in frames:
        check("StreamEnvelope", data, label="SSE frame")


def test_a_ticket_mutation_arrives_as_ticket_changed_with_the_record(
        server, principal, project, operator, enrolled, ticket):
    before = server.events.start_position(project["id"], None)[0]
    enrolled["client"].post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]})
    frames = collect(server, principal, project["id"],
                     last_event_id="evt_{:012d}_aaaaaa".format(before),
                     max_frames=1)
    sse_id, kind, data = frames[0]
    assert kind == "ticket_changed"
    assert data["subject_id"] == ticket["id"]
    assert data["payload"]["state"] == "claimed"
    # The SSE id is the event id, which is what Last-Event-ID resumes from.
    assert sse_id == data["event_id"]


def test_replay_starts_after_the_supplied_id_not_at_it(server, principal,
                                                       project, operator):
    for index in range(3):
        operator.post("/tickets", {"request_id": rid(),
                                   "title": "T{}".format(index),
                                   "outcome": "o", "acceptance": [{"text": "a"}]})
    everything = collect(server, principal, project["id"],
                         last_event_id="evt_000000000000_aaaaaa", max_frames=10)
    assert len(everything) >= 4
    cursor = everything[1][0]
    resumed = collect(server, principal, project["id"], last_event_id=cursor,
                      max_frames=10)
    # The frame the client already applied is not sent again, and nothing
    # between it and the head is skipped.
    assert [f[0] for f in resumed] == [f[0] for f in everything[2:]]


def test_no_cursor_starts_at_the_head_rather_than_replaying_the_board(
        server, principal, project, operator):
    for index in range(3):
        operator.post("/tickets", {"request_id": rid(),
                                   "title": "T{}".format(index), "outcome": "o",
                                   "acceptance": [{"text": "a"}]})
    frames = collect(server, principal, project["id"], max_frames=1, timeout=0.4)
    assert frames == [] or frames[0][1] == "heartbeat"


def test_an_aged_out_cursor_gets_one_snapshot_required(server, principal,
                                                       project, operator):
    """Silently starting from head would leave the dashboard permanently wrong."""
    for index in range(4):
        operator.post("/tickets", {"request_id": rid(),
                                   "title": "T{}".format(index), "outcome": "o",
                                   "acceptance": [{"text": "a"}]})
    narrow = EventStream(server.store, retention=1, heartbeat_seconds=0.05)
    frames = collect(server, principal, project["id"],
                     last_event_id="evt_000000000001_aaaaaa", max_frames=1,
                     stream=narrow)
    sse_id, kind, data = frames[0]
    assert kind == "snapshot_required"
    assert data["resume_hint"] == {"reason": "cursor_expired"}
    # No id: line. A client that stored this as its cursor would resume from a
    # position the trail never contained.
    assert sse_id is None
    check("StreamEnvelope", data)


def test_an_unparseable_cursor_is_treated_as_expired(server, principal, project,
                                                      operator, ticket):
    frames = collect(server, principal, project["id"],
                     last_event_id="not-an-event-id", max_frames=1)
    assert frames[0][1] == "snapshot_required"


def test_heartbeats_are_emitted_and_carry_no_resumable_id(server, principal,
                                                           project):
    quick = EventStream(server.store, heartbeat_seconds=0.05, poll_seconds=0.01)
    frames = collect(server, principal, project["id"], max_frames=2,
                     stream=quick, timeout=1.0)
    assert frames and all(kind == "heartbeat" for _, kind, _ in frames)
    assert all(sse_id is None for sse_id, _, _ in frames)
    for _, _, data in frames:
        check("StreamEnvelope", data, label="heartbeat")
        assert data["payload"] is None


def test_a_subscriber_never_sees_another_projects_events(server, principal,
                                                          project, operator):
    other = server.store.create_project("Other")
    other_session = server.bootstrap_operator(other["id"])
    other_client = Client(server, project_id=other["id"],
                          cookie=other_session["session_token"],
                          csrf=other_session["csrf_token"])
    before = server.events.start_position(project["id"], None)[0]
    other_client.post("/tickets", {"request_id": rid(), "title": "Theirs",
                                   "outcome": "o", "acceptance": [{"text": "a"}]})
    frames = collect(server, principal, project["id"],
                     last_event_id="evt_{:012d}_aaaaaa".format(before),
                     max_frames=1, timeout=0.4)
    assert [f for f in frames if f[1] != "heartbeat"] == []


def test_event_ids_sort_lexicographically_in_sequence_order(server, principal,
                                                            project, operator):
    """The property `Last-Event-ID` resume correctness rests on."""
    for index in range(12):
        operator.post("/tickets", {"request_id": rid(),
                                   "title": "T{}".format(index), "outcome": "o",
                                   "acceptance": [{"text": "a"}]})
    ids = [f[0] for f in collect(server, principal, project["id"],
                                 last_event_id="evt_000000000000_aaaaaa",
                                 max_frames=20)]
    assert ids == sorted(ids)
    assert [seq_of(i) for i in ids] == sorted(seq_of(i) for i in ids)


def test_a_payload_that_cannot_be_resolved_does_not_kill_the_stream(
        server, principal, project, operator, ticket):
    """A row that vanished between the audit write and the read yields null."""
    before = server.events.start_position(project["id"], None)[0]
    operator.post("/tickets/{}/blocked".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "blocked": True, "reason": "r"})
    server.store.conn.execute("DELETE FROM ticket_dependencies")
    server.store.conn.execute("DELETE FROM tickets WHERE id = ?", (ticket["id"],))
    frames = collect(server, principal, project["id"],
                     last_event_id="evt_{:012d}_aaaaaa".format(before),
                     max_frames=1)
    assert frames[0][1] == "ticket_changed"
    assert frames[0][2]["payload"] is None
    check("StreamEnvelope", frames[0][2])
