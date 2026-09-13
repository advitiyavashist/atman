"""T-185 release bar, over a real socket, unique request_ids, no live board.

Maps docs/interface-v1.md release acceptance onto HTTP against serve():
two enrolled sessions get different tickets; eight claims yield one owner;
two masters yield one lease and a takeover advances the epoch; hook retries
dedupe; expired leases cannot claim; revocation keeps claimed work;
hook failure is visible while a healthy peer still claims.
"""

from __future__ import annotations

import json
import threading
import uuid

from tests.acceptance.messaging.live_board import rid
from ticket_board.server.auth import in_seconds


def _hook(agent, *, event_id=None, kind="session_start"):
    return agent.http.post("/hook-events", {
        "request_id": rid(),
        "event": {
            "event_id": event_id or ("hev_" + uuid.uuid4().hex),
            "agent_id": agent.agent_id,
            "session_id": agent.session_id,
            "kind": kind,
            "occurred_at": "2026-09-10T00:00:00Z",
        },
    })


def _agent_row(board, agent_id):
    items = board.operator.get("/agents").json()["items"]
    return next(a for a in items if a["id"] == agent_id)


def _ticket(board, ticket_id):
    body = board.operator.get("/tickets/" + ticket_id).json()
    return body.get("ticket") or body


def test_two_enrolled_sessions_each_claim_a_different_ticket(board):
    """Disposable two-session proof. Real Claude is optional (T185_LIVE_CLAUDE).

    Two enroll->exchange chains, two open tickets, each session claims one
    and only its own. This is the V1 'two Claude sessions connect and each
    gets a different eligible ticket' bar without touching a live board.
    """
    left = board.enroll("claude-a", max_active_tickets=1)
    right = board.enroll("claude-b", max_active_tickets=1)
    t1 = board.ticket("T185-left")
    t2 = board.ticket("T185-right")

    assert _hook(left).status == 200
    assert _hook(right).status == 200

    c1 = left.http.post("/tickets/{}/claim".format(t1["id"]), {
        "request_id": rid(), "expected_version": t1["version"],
        "session_id": left.session_id})
    c2 = right.http.post("/tickets/{}/claim".format(t2["id"]), {
        "request_id": rid(), "expected_version": t2["version"],
        "session_id": right.session_id})
    assert c1.status == 200, c1.body
    assert c2.status == 200, c2.body
    assert c1.json()["owner"] == left.agent_id
    assert c2.json()["owner"] == right.agent_id
    assert c1.json()["id"] != c2.json()["id"]

    cross = left.http.post("/tickets/{}/claim".format(t2["id"]), {
        "request_id": rid(), "expected_version": c2.json()["version"],
        "session_id": left.session_id})
    assert cross.status == 409, cross.body
    assert cross.json()["error"]["code"] in (
        "ticket_already_claimed", "capacity_exhausted")


def test_eight_http_claims_yield_exactly_one_owner(board):
    ticket = board.ticket("T185-race")
    agents = [
        board.enroll("racer-{}".format(i), max_active_tickets=1)
        for i in range(8)
    ]
    results = []
    errors = []
    barrier = threading.Barrier(len(agents))

    def race(agent):
        try:
            barrier.wait(timeout=10)
            results.append(agent.http.post(
                "/tickets/{}/claim".format(ticket["id"]),
                {"request_id": rid(), "expected_version": ticket["version"],
                 "session_id": agent.session_id}))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=race, args=(a,)) for a in agents]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(results) == 8
    winners = [r for r in results if r.status == 200]
    losers = [r for r in results if r.status != 200]
    assert len(winners) == 1, [(r.status, r.body) for r in results]
    for resp in losers:
        assert resp.status == 409, resp.body
        assert resp.json()["error"]["code"] in (
            "ticket_already_claimed", "ticket_version_conflict")
        assert "database is locked" not in resp.json()["error"]["message"].lower()

    final = _ticket(board, ticket["id"])
    assert final["owner"] == winners[0].json()["owner"]
    claims = board.store.conn.execute(
        "SELECT COUNT(*) FROM audit_events WHERE action = 'ticket.claim'"
        " AND subject_id = ?", (ticket["id"],)).fetchone()[0]
    assert claims == 1


def test_master_failover_one_holder_then_takeover_advances_epoch(board):
    first = board.operator
    second = board.second_operator("failover-op")

    taken = first.post("/master/lease", {
        "request_id": rid(), "expected_epoch": 0})
    assert taken.status == 200, taken.body
    assert taken.json()["epoch"] == 1

    stale = second.post("/master/lease", {
        "request_id": rid(), "expected_epoch": 0})
    assert stale.status == 409, stale.body
    assert stale.json()["error"]["code"] == "master_lease_conflict"
    assert stale.json()["error"]["details"]["actual_epoch"] == 1

    takeover = second.post("/master/lease", {
        "request_id": rid(), "expected_epoch": 1})
    assert takeover.status == 200, takeover.body
    assert takeover.json()["epoch"] == 2

    ghost = first.post("/master/pause", {
        "request_id": rid(), "lease_epoch": 1, "paused": True})
    assert ghost.status == 409, ghost.body
    assert ghost.json()["error"]["code"] == "master_lease_expired"


def test_retried_hook_event_is_one_audit_record(board):
    agent = board.enroll("hook-agent")
    event_id = "hev_" + uuid.uuid4().hex
    first = _hook(agent, event_id=event_id)
    second = _hook(agent, event_id=event_id)
    assert first.status == 200 and first.json()["deduplicated"] is False
    assert second.status == 200 and second.json()["deduplicated"] is True
    rows = board.store.conn.execute(
        "SELECT COUNT(*) FROM hook_events WHERE event_id = ?",
        (event_id,)).fetchone()[0]
    assert rows == 1
    audits = board.audit("hook.", subject_id=agent.agent_id)
    assert len(audits) == 1


def test_hook_failure_is_visible_and_a_healthy_peer_still_claims(board):
    broken = board.enroll("hook-broken")
    healthy = board.enroll("hook-healthy")
    ticket = board.ticket("T185-offline")

    health = dict(_agent_row(board, broken.agent_id).get("hook_health") or {})
    health["last_error"] = "adapter unreachable"
    board.store.conn.execute(
        "UPDATE agents SET hook_health = ? WHERE id = ?",
        (json.dumps(health), broken.agent_id))
    board.store.conn.commit()

    shown = _agent_row(board, broken.agent_id)
    assert shown["state"] == "offline", shown

    board.store.conn.execute(
        "UPDATE session_leases SET expires_at = ? WHERE session_id = ?",
        (in_seconds(-60), broken.session_id))
    board.store.conn.commit()

    refused = broken.http.post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": broken.session_id})
    assert refused.status == 409, refused.body
    assert refused.json()["error"]["code"] == "session_lease_expired"

    ok = healthy.http.post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": healthy.session_id})
    assert ok.status == 200, ok.body
    assert ok.json()["owner"] == healthy.agent_id


def test_expired_lease_cannot_claim_or_update(board):
    agent = board.enroll("lease-agent")
    ticket = board.ticket("T185-lease")
    claimed = agent.http.post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": agent.session_id})
    assert claimed.status == 200, claimed.body

    board.store.conn.execute(
        "UPDATE session_leases SET expires_at = ? WHERE session_id = ?",
        (in_seconds(-60), agent.session_id))
    board.store.conn.commit()

    other = board.ticket("T185-lease-2")
    again = agent.http.post("/tickets/{}/claim".format(other["id"]), {
        "request_id": rid(), "expected_version": other["version"],
        "session_id": agent.session_id})
    assert again.status == 409
    assert again.json()["error"]["code"] == "session_lease_expired"

    update = agent.http.post("/tickets/{}/updates".format(ticket["id"]), {
        "request_id": rid(), "body": "offline write", "next_step": "n",
        "session_id": agent.session_id})
    assert update.status in (409, 401), update.body
    if update.status == 409:
        assert update.json()["error"]["code"] == "session_lease_expired"


def test_revocation_retains_claimed_work_and_does_not_reassign(board):
    agent = board.enroll("keep-work")
    ticket = board.ticket("T185-keep")
    claimed = agent.http.post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": agent.session_id})
    assert claimed.status == 200, claimed.body
    note = agent.http.post("/tickets/{}/updates".format(ticket["id"]), {
        "request_id": rid(), "body": "progress that must survive",
        "next_step": "keep going", "session_id": agent.session_id})
    assert note.status == 201, note.body

    version = _agent_row(board, agent.agent_id)["version"]
    revoked = board.operator.delete(
        "/agents/{}/session-lease".format(agent.agent_id),
        {"request_id": rid(), "expected_version": version,
         "note": "Unreachable."})
    assert revoked.status == 200, revoked.body

    after = _ticket(board, ticket["id"])
    assert after["state"] == "claimed"
    assert after["owner"] == agent.agent_id
    detail = board.operator.get("/tickets/" + ticket["id"]).json()
    updates = detail.get("updates") or detail.get("ticket", {}).get("updates") or []
    texts = [u.get("body") or u.get("text") or "" for u in updates]
    if texts:
        assert any("progress that must survive" in t for t in texts)
    else:
        trail = board.store.conn.execute(
            "SELECT body FROM ticket_updates WHERE ticket_id = ?",
            (ticket["id"],)).fetchall()
        assert any("progress that must survive" in r["body"] for r in trail)

    peer = board.enroll("successor")
    steal = peer.http.post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": after["version"],
        "session_id": peer.session_id})
    assert steal.status == 409, steal.body
    assert steal.json()["error"]["code"] == "ticket_already_claimed"
