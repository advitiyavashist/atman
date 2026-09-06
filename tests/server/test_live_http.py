"""The two behaviours where the socket *is* the behaviour.

Everything else in `tests/server/` drives the router in process, which is
faster and deterministic. These two cannot be: an SSE reconnect is only a
reconnect if the connection really dropped, and a claim race only proves
anything if the requests really are concurrent.
"""

import json
import http.client
import threading
import time
import uuid

import pytest

from api_client import Client, rid
from ticket_board.server.httpd import make_server

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


@pytest.fixture()
def live(server, project):
    httpd = make_server(server, host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield "127.0.0.1", httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def call(live, method, path, *, headers, body=None, timeout=10):
    conn = http.client.HTTPConnection(live[0], live[1], timeout=timeout)
    try:
        payload = json.dumps(body).encode() if body is not None else None
        head = dict(headers)
        if payload is not None:
            head["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=head)
        response = conn.getresponse()
        raw = response.read()
        return response.status, (json.loads(raw) if raw else None)
    finally:
        conn.close()


class Subscription:
    """A live `GET /events` connection, read frame by frame."""

    def __init__(self, live, headers, last_event_id=None):
        self.conn = http.client.HTTPConnection(live[0], live[1], timeout=10)
        head = dict(headers)
        if last_event_id:
            head["Last-Event-ID"] = last_event_id
        self.conn.request("GET", "/events", headers=head)
        self.response = self.conn.getresponse()
        assert self.response.status == 200
        assert self.response.getheader("Content-Type") == "text/event-stream"

    def frames(self, count, *, deadline=8.0):
        """Read up to `count` non-heartbeat frames."""
        found, block, stop = [], [], time.monotonic() + deadline
        while len(found) < count and time.monotonic() < stop:
            line = self.response.readline()
            if not line:
                break
            line = line.decode("utf-8").rstrip("\n")
            if line:
                block.append(line)
                continue
            frame = _frame(block)
            block = []
            if frame and frame["type"] != "heartbeat":
                found.append(frame)
        return found

    def close(self):
        self.conn.close()


def _frame(lines):
    sse_id = kind = data = None
    for line in lines:
        if line.startswith("id: "):
            sse_id = line[4:]
        elif line.startswith("event: "):
            kind = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
    if data is None:
        return None
    return {"sse_id": sse_id, "type": kind, "data": data}


# --------------------------------------------------------------- reconnect

def test_a_reconnect_resumes_without_a_gap_or_a_duplicate(live, operator,
                                                           enrolled, ticket):
    """The whole point of the durable cursor, over a real dropped connection."""
    agent_headers = {"X-Project-Id": enrolled["client"].project_id,
                     "Authorization": "Bearer " + enrolled["token"]}
    operator_headers = {k: v for k, v in operator.headers().items()
                        if k in ("X-Project-Id", "Cookie")}

    subscription = Subscription(live, operator_headers)
    time.sleep(0.3)   # let the stream reach its poll loop before mutating

    status, claimed = call(live, "POST", "/tickets/{}/claim".format(ticket["id"]),
                           headers=agent_headers,
                           body={"request_id": rid(),
                                 "expected_version": ticket["version"],
                                 "session_id": enrolled["session_id"]})
    assert status == 200, claimed

    first = subscription.frames(1)
    assert len(first) == 1
    assert first[0]["type"] == "ticket_changed"
    cursor = first[0]["sse_id"]
    assert cursor and cursor == first[0]["data"]["event_id"]

    # The dashboard's laptop sleeps.
    subscription.close()

    status, _ = call(live, "POST", "/tickets/{}/updates".format(ticket["id"]),
                     headers=agent_headers,
                     body={"request_id": rid(), "body": "kept working",
                           "next_step": "finish the decoder",
                           "session_id": enrolled["session_id"]})
    assert status == 201

    resumed = Subscription(live, operator_headers, last_event_id=cursor)
    try:
        missed = resumed.frames(1)
    finally:
        resumed.close()

    assert len(missed) == 1, "the event that happened while disconnected was lost"
    # No gap: the update that landed while the client was away arrives.
    assert missed[0]["data"]["subject_id"] == ticket["id"]
    # No duplicate: the frame the client already applied is not resent.
    assert missed[0]["sse_id"] != cursor
    assert missed[0]["sse_id"] > cursor


def test_a_stream_needs_a_credential(live, project):
    conn = http.client.HTTPConnection(live[0], live[1], timeout=5)
    conn.request("GET", "/events", headers={"X-Project-Id": project["id"]})
    response = conn.getresponse()
    assert response.status == 401
    conn.close()


# -------------------------------------------------------------- claim race

def test_racing_claims_over_http_yield_exactly_one_owner(live, server, project,
                                                          operator):
    """Eight real HTTP claims on one ticket. One 200, seven contract 409s.

    The store's own race test proves the transaction is atomic. This proves the
    route does not undo that -- a read-modify-write in a handler, or a shared
    connection across threads, would show up here and nowhere else.
    """
    ticket = operator.post("/tickets", {
        "request_id": rid(), "title": "Contended", "outcome": "o",
        "acceptance": [{"text": "a"}]}).json()

    agents = [_enroll(server, operator, project, "racer-{}".format(i))
              for i in range(8)]
    results, errors = [], []
    barrier = threading.Barrier(len(agents))

    def race(agent):
        try:
            barrier.wait(timeout=10)
            status, body = call(
                live, "POST", "/tickets/{}/claim".format(ticket["id"]),
                headers={"X-Project-Id": project["id"],
                         "Authorization": "Bearer " + agent["token"]},
                body={"request_id": rid(),
                      "expected_version": ticket["version"],
                      "session_id": agent["session_id"]})
            results.append((agent["agent_id"], status, body))
        except Exception as exc:                       # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=race, args=(a,)) for a in agents]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(results) == len(agents)
    winners = [r for r in results if r[1] == 200]
    losers = [r for r in results if r[1] != 200]
    assert len(winners) == 1, [(r[1], r[2]) for r in results]

    # Every loser gets a contract error, not an opaque driver failure.
    for agent_id, status, body in losers:
        assert status == 409, (agent_id, status, body)
        assert body["error"]["code"] in ("ticket_already_claimed",
                                         "ticket_version_conflict"), body
        assert "database is locked" not in body["error"]["message"].lower()

    final = operator.get("/tickets/" + ticket["id"]).json()["ticket"]
    assert final["owner"] == winners[0][0]
    assert final["version"] == ticket["version"] + 1

    # Exactly one claim in the trail: a second winner would have audited too.
    claims = server.store.conn.execute(
        "SELECT COUNT(*) FROM audit_events WHERE action = 'ticket.claim'"
        " AND subject_id = ?", (ticket["id"],)).fetchone()[0]
    assert claims == 1


def _enroll(server, operator, project, name):
    created = operator.post("/enrollments", {
        "request_id": rid(), "agent_name": name, "role": "backend"})
    session_id = "ses_" + uuid.uuid4().hex[:8]
    payload = Client(server, project_id=project["id"]).post("/sessions", {
        "request_id": rid(), "code": created.json()["code"],
        "session_id": session_id}).json()
    return {"agent_id": payload["agent"]["id"], "session_id": session_id,
            "token": payload["token"]}
