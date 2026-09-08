"""T-232: T-185 pre-work -- connection/concurrency/agent-recovery cases that
do NOT require a live deployed board, run today against T-180's real server
code (BoardServer + a real socket via `live`/`Subscription`, the same
machinery test_live_http.py and T-225 already use -- not the fixture stub).

Every case here is either:
  PROVEN   -- runs today, asserts against real behaviour, carries over to a
              live BOARD_URL unchanged (these tests talk pure HTTP+SSE).
  GAP      -- named and explained, cannot run without something T-185 alone
              has (a genuinely separate deployed process, a real spawned
              agent session, or a component not yet built).

Named GAP cases cannot run without a separate deployed process, a real
spawned agent session, or a component not yet built.
"""

import http.client
import json
import threading
import time
import uuid

import pytest

from api_client import Client, rid
from test_live_http import Subscription, call, live  # noqa: F401


def _enroll(server, operator, project, name):
    created = operator.post("/enrollments", {
        "request_id": rid(), "agent_name": name, "role": "backend",
        "connection_mode": "managed"})
    assert created.status == 201, created.json()
    session_id = "ses_" + uuid.uuid4().hex[:8]
    exchanged = Client(server, project_id=project["id"]).post("/sessions", {
        "request_id": rid(), "code": created.json()["code"],
        "session_id": session_id})
    assert exchanged.status == 201, exchanged.json()
    payload = exchanged.json()
    return {"agent_id": payload["agent"]["id"], "session_id": session_id,
            "token": payload["token"],
            "client": Client(server, project_id=project["id"],
                             token=payload["token"], origin=None)}


# ============================================================= CONNECTION

def test_enroll_exchange_hookevent_claim_is_one_continuous_chain(
        server, operator, project, ticket):
    """PROVEN. The connection path T-181's adapter drives end to end, over
    the router (no stub): enrollment code -> session exchange -> a real hook
    event under that session -> a claim using the same session_id.
    """
    agent = _enroll(server, operator, project, "chain-agent")

    hook = agent["client"].post("/hook-events", {
        "request_id": rid(),
        "event": {"event_id": "hev_" + uuid.uuid4().hex,
                  "agent_id": agent["agent_id"], "session_id": agent["session_id"],
                  "kind": "session_start", "occurred_at": "2026-09-07T00:00:00Z"}})
    assert hook.status in (200, 201), hook.json()

    claimed = agent["client"].post(
        "/tickets/{}/claim".format(ticket["id"]),
        {"request_id": rid(), "expected_version": ticket["version"],
         "session_id": agent["session_id"]})
    assert claimed.status == 200, claimed.json()
    assert claimed.json()["owner"] == agent["agent_id"]


def test_a_revoked_agent_token_paired_with_a_valid_operator_cookie_is_still_401(
        server, project, operator, enrolled):
    """PROVEN, completes T-225's credential-alternatives matrix with a
    genuinely REVOKED (not just garbage) bearer, per the T-232 brief's
    explicit ask: 'a valid credential of one kind paired with an
    expired/invalid one of the other'. auth.py's own accounting is that a
    revoked token still authenticates a Principal object (row exists) but is
    caught by the `revoked_at is not None` branch in `_agent_principal` --
    same Unauthenticated-not-fallthrough path T-225 proved for a garbage
    token, now proven for the credential's own real lifecycle event.
    """
    server.credentials.revoke_agent_tokens_for_session(enrolled["session_id"])
    operator_session = server.bootstrap_operator(project["id"])
    combined = enrolled["client"].with_(
        cookie=operator_session["session_token"], csrf=operator_session["csrf_token"])
    response = combined.get("/overview")
    assert response.status == 401, response.json()


def test_an_expired_operator_session_paired_with_a_valid_agent_token_is_still_401(
        server, project, enrolled):
    """PROVEN. The other direction, with genuine TTL expiry rather than a
    garbage cookie: authenticate_all's `_operator_principal` raises
    Unauthenticated on `expires_at <= now`, same as a bad hash -- so an
    agent's otherwise-valid bearer does not rescue a request carrying a
    stale operator cookie, and vice versa is not tested here (T-225 already
    covers bad-bearer-with-good-cookie both ways for the garbage case).
    """
    operator = server.bootstrap_operator(project["id"], display_name="Expiring")
    # Mint with -1s TTL: already expired the instant it exists.
    import ticket_board.server.auth as auth_mod
    raw = auth_mod.mint_secret(32)
    csrf = auth_mod.mint_secret(24)
    server.credentials.conn.execute(
        "INSERT INTO operator_sessions (token_hash, operator_id, project_id,"
        " csrf_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
        (auth_mod.hash_secret(raw), operator["operator_id"], project["id"],
         auth_mod.hash_secret(csrf), auth_mod.now(), auth_mod.in_seconds(-1)),
    )
    combined = enrolled["client"].with_(cookie=raw, csrf=csrf)
    response = combined.get("/overview")
    assert response.status == 401, response.json()


# ============================================================= CONCURRENCY

def test_the_three_claim_outcome_paths_all_hold_over_real_http(
        live, server, project, operator, ticket):
    """PROVEN. The triad T-180's handoff names explicitly, each over a real
    socket with genuinely distinct agents/sessions, not the store-level
    `_reassign` shortcut test_auth_and_scope.py uses for the first case.
    """
    owner = _enroll(server, operator, project, "owner-agent")
    other = _enroll(server, operator, project, "other-agent")

    claimed = owner["client"].post(
        "/tickets/{}/claim".format(ticket["id"]),
        {"request_id": rid(), "expected_version": ticket["version"],
         "session_id": owner["session_id"]})
    assert claimed.status == 200, claimed.json()
    v1 = claimed.json()["version"]

    # (1) a DIFFERENT agent writing to a ticket it never owned -> 403.
    status, body = call(live, "POST", "/tickets/{}/updates".format(ticket["id"]),
                        headers={"X-Project-Id": project["id"],
                                 "Authorization": "Bearer " + other["token"]},
                        body={"request_id": rid(), "body": "not mine",
                              "next_step": "n"})
    assert status == 403, body
    assert body["error"]["code"] == "forbidden_scope"

    # (2) the SAME agent, stale SESSION (master-loop-style reassignment
    # simulated through the store, as test_auth_and_scope.py's own helper
    # does since T-182's route does not exist yet) -> 201 superseded=true.
    new_session = "ses_" + uuid.uuid4().hex[:8]
    server.store.conn.execute(
        "UPDATE tickets SET owner_session = ? WHERE id = ?",
        (new_session, ticket["id"]))
    status, body = call(live, "POST", "/tickets/{}/updates".format(ticket["id"]),
                        headers={"X-Project-Id": project["id"],
                                 "Authorization": "Bearer " + owner["token"]},
                        body={"request_id": rid(), "body": "still here",
                              "next_step": "n", "session_id": owner["session_id"]})
    assert status == 201, body
    assert body["superseded"] is True

    # (3) a STALE CLAIM (wrong expected_version) -> 409 ticket_version_conflict.
    status, body = call(live, "POST", "/tickets/{}/claim".format(ticket["id"]),
                        headers={"X-Project-Id": project["id"],
                                 "Authorization": "Bearer " + other["token"]},
                        body={"request_id": rid(), "expected_version": v1 - 1,
                              "session_id": other["session_id"]})
    assert status == 409, body
    assert body["error"]["code"] == "ticket_version_conflict"


def test_master_lease_contention_exactly_one_racer_wins(live, server, project):
    """PROVEN. Two operators race POST /master/lease at the same
    expected_epoch=0. master.take_lease's CAS is inside one immediate
    transaction (master.py:45-96): the loser must see the winner's epoch and
    get 409 master_lease_conflict, never two epoch=1 winners.
    """
    sessions = [server.bootstrap_operator(project["id"], display_name="op-{}".format(i))
                for i in range(6)]
    results = []
    barrier = threading.Barrier(len(sessions))

    def race(sess):
        barrier.wait(timeout=10)
        status, body = call(live, "POST", "/master/lease",
                            headers={"X-Project-Id": project["id"],
                                     "Cookie": "tb_session=" + sess["session_token"],
                                     "X-CSRF-Token": sess["csrf_token"],
                                     "Origin": "http://127.0.0.1:4319"},
                            body={"request_id": rid(), "expected_epoch": 0})
        results.append((status, body))

    threads = [threading.Thread(target=race, args=(s,)) for s in sessions]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert len(results) == len(sessions)
    winners = [r for r in results if r[0] == 200]
    losers = [r for r in results if r[0] != 200]
    assert len(winners) == 1, results
    assert winners[0][1]["epoch"] == 1
    for status, body in losers:
        assert status == 409, body
        assert body["error"]["code"] == "master_lease_conflict"
        assert body["error"]["details"]["actual_epoch"] == 1


def test_master_lease_expiry_is_reported_but_is_not_a_fencing_condition(
        server, project, operator):
    """PROVEN, and a deliberately-designed-this-way behaviour, not a defect:
    master.take_lease (master.py:53-56) checks the EPOCH, never expiry --
    the docstring is explicit that requiring expiry too would let a live but
    unresponsive master hold the board hostage. So an expired lease must
    still (a) be re-takeable by the correct expected_epoch and (b) show up
    as a master_lease_expired attention item in the meantime (the exact bug
    an earlier pass fixed mid-T-180: it was keyed on 'holder is not null and
    expired', but serialize_master_lease nulls holder once a lease lapses,
    so the alert could never fire -- now keyed on epoch>0 and expired).
    """
    taken = operator.post("/master/lease", {"request_id": rid(), "expected_epoch": 0})
    assert taken.status == 200, taken.json()
    server.store.conn.execute(
        "UPDATE master_lease SET expires_at = '2000-01-01T00:00:00Z'"
        " WHERE project_id = ?", (project["id"],))

    overview = operator.get("/overview")
    kinds = [item["kind"] for item in overview.json()["attention"]]
    assert "master_lease_expired" in kinds, overview.json()["attention"]

    retaken = operator.post("/master/lease", {"request_id": rid(), "expected_epoch": 1})
    assert retaken.status == 200, retaken.json()
    assert retaken.json()["epoch"] == 2


def test_sse_reconnect_resumes_across_a_genuine_server_restart(tmp_path, project):
    """PROVEN, and stronger than test_live_http.py's own reconnect test:
    that one drops the TCP connection but keeps the same BoardServer/db
    connection alive in the same process. This kills the httpd AND
    constructs a brand-new BoardServer instance against the SAME sqlite
    file, which is what a real master-process restart/redeploy looks like.
    Proves the durable cursor survives the server object dying, not just
    the socket.
    """
    from ticket_board.server import BoardServer
    from ticket_board.server.httpd import make_server

    db_path = tmp_path / "board.sqlite3"
    server1 = BoardServer(db_path)
    proj = server1.store.create_project(project["name"] if False else "Restart-Demo")
    op_session = server1.bootstrap_operator(proj["id"])
    operator1 = Client(server1, project_id=proj["id"], cookie=op_session["session_token"],
                       csrf=op_session["csrf_token"])

    httpd1 = make_server(server1, host="127.0.0.1", port=0)
    thread1 = threading.Thread(target=httpd1.serve_forever, daemon=True)
    thread1.start()
    live1 = ("127.0.0.1", httpd1.server_address[1])
    op_headers = {"X-Project-Id": proj["id"], "Cookie": "tb_session=" + op_session["session_token"]}

    sub = Subscription(live1, op_headers)
    time.sleep(0.2)   # let the stream reach its poll loop before mutating
    ticket = operator1.post("/tickets", {
        "request_id": rid(), "title": "before restart", "outcome": "o",
        "acceptance": [{"text": "a"}]}).json()
    first = sub.frames(1)
    assert len(first) == 1
    cursor = first[0]["sse_id"]
    sub.close()
    httpd1.shutdown()
    httpd1.server_close()
    thread1.join(timeout=5)
    server1.close()

    # A brand-new process would open a new BoardServer over the same file.
    server2 = BoardServer(db_path)
    operator2 = Client(server2, project_id=proj["id"], cookie=op_session["session_token"],
                       csrf=op_session["csrf_token"])
    posted = operator2.post("/tickets/{}/updates".format(ticket["id"]), {
        "request_id": rid(), "body": "after restart", "next_step": "n"})
    # No live agent owns it, so this specific call may 403/other depending on
    # ownership -- the point under test is only that the SERVER and its
    # durable event log survived, which the resumed stream below proves
    # regardless of this call's own status.
    httpd2 = make_server(server2, host="127.0.0.1", port=0)
    thread2 = threading.Thread(target=httpd2.serve_forever, daemon=True)
    thread2.start()
    live2 = ("127.0.0.1", httpd2.server_address[1])
    try:
        resumed = Subscription(live2, op_headers, last_event_id=cursor)
        try:
            missed = resumed.frames(1, deadline=5)
        finally:
            resumed.close()
        assert len(missed) == 1, "durable cursor did not survive a server restart"
        assert missed[0]["sse_id"] > cursor
    finally:
        httpd2.shutdown()
        httpd2.server_close()
        thread2.join(timeout=5)
        server2.close()


# ========================================================= AGENT RECOVERY
#
# There is no route-level "agent recovery" endpoint in the frozen contract;
# recovery is READ through GET /agents' `state` field, computed at read time
# by `derive_agent_state` (server/views.py:207-229). This is E-010's own
# analog of the exact liveness-signal problem T-230 diagnosed in tickets.py
# itself: a value computed from stale proxies (heartbeat age, lease
# revocation, hook health) that a dashboard renders as a confident state
# word. T-230's taxonomy (working/idle/limited/dead/unknown, unknown must be
# its own state) is the right lens for grading `derive_agent_state`, not a
# coincidence -- the frozen AgentState enum (openapi.yaml:503-505) is
# [connected, idle, working, awaiting-input, offline, revoked], already
# flagged by T-229/T-224 as having no `unknown`/`limited` equivalent.

def test_agent_state_working_idle_offline_and_revoked_are_each_reachable(
        server, project, operator, ticket):
    """PROVEN. Exercises 4 of the 5 reachable branches of derive_agent_state
    over the real GET /agents route. `connected` and `awaiting-input` are
    not exercised here -- see the GAP note below.
    """
    agent = _enroll(server, operator, project, "state-agent")

    idle = operator.get("/agents").json()["items"][0]
    assert idle["state"] == "idle"

    claimed = agent["client"].post(
        "/tickets/{}/claim".format(ticket["id"]),
        {"request_id": rid(), "expected_version": ticket["version"],
         "session_id": agent["session_id"]})
    assert claimed.status == 200, claimed.json()
    working = operator.get("/agents").json()["items"][0]
    assert working["state"] == "working"

    server.store.conn.execute(
        "UPDATE agents SET last_heartbeat_at = '2000-01-01T00:00:00Z' WHERE id = ?",
        (agent["agent_id"],))
    offline = operator.get("/agents").json()["items"][0]
    assert offline["state"] == "offline"

    server.credentials.revoke_agent_tokens_for_session(agent["session_id"])
    server.store.conn.execute(
        "UPDATE session_leases SET revoked_at = ? WHERE session_id = ?",
        (server.store.conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%SZ','now')").fetchone()[0],
         agent["session_id"]))
    revoked = operator.get("/agents").json()["items"][0]
    assert revoked["state"] == "revoked"


def test_an_agent_with_no_signal_at_all_renders_as_idle_not_unknown(
        server, project, operator):
    """DECLARED GAP, proven as a gap rather than asserted as a defect.

    derive_agent_state (server/views.py:207-229) has no `unknown` branch: an
    agent enrolled but never connected -- no session lease ever created, no
    heartbeat, no hook_health, no capacity -- falls through every `if` to
    the unconditional `return "idle"` on the last line. This is the same
    shape of bug T-230 diagnosed in tickets.py's own liveness code (a
    missing-data case silently rendered as a confident, healthy-looking
    state instead of its own `unknown`), and it is a FROZEN-CONTRACT
    limitation, not a T-180 defect: AgentState's enum
    (openapi.yaml:503-505) has no `unknown` member for the route layer to
    return even if it wanted to. Already tracked (T-229's audit, landing in
    T-224's amendment), not re-filed here -- this test exists so T-185 has a
    concrete, checkable assertion for TODAY's behaviour, and so the day
    T-224 adds an `unknown` member, this test's expected value flips and
    that flip IS the acceptance signal that the contract gap closed.
    """
    created = operator.post("/enrollments", {
        "request_id": rid(), "agent_name": "never-connected", "role": "backend"})
    assert created.status == 201, created.json()
    agents = operator.get("/agents").json()["items"]
    never_connected = [a for a in agents if a["name"] == "never-connected"][0]
    assert never_connected["session"] is None
    assert never_connected["state"] == "idle", (
        "if this ever changes, it is because T-224 landed an `unknown` "
        "state -- update this test to assert THAT, do not just re-pin idle"
    )


# ================================================================= GAPS
#
# Named explicitly per the T-232 brief ("state plainly what needs a live
# server"). None of these can be proven by this file.

def test_gap_clone_worktree_registry_both_directions():
    pytest.skip(
        "GAP: T-205's confirmed clone-worktree hole (a worktree of a CLONE "
        "of a protected checkout, outside the protected roots, passes both "
        "git-identity and path-containment and would be enrolled) is scoped "
        "to a live-checkout registry in T-192, which does not exist yet. "
        "T-185 must assert the registry refuses that exact clone case AND "
        "still allows an ordinary project -- both directions, because a "
        "registry that refuses everything is not a pass either. Cannot be "
        "written until T-192 lands; tracked, not silently dropped."
    )


def test_gap_real_agent_process_liveness_cannot_be_simulated_here():
    pytest.skip(
        "GAP: T-230's working/idle/limited/dead/unknown taxonomy is ground "
        "-truthed against REAL Claude/Codex/Cursor transcript files and "
        "watch.log run history for actually-spawned processes. This file "
        "can only exercise E-010's OWN read-time proxy (derive_agent_state) "
        "against a synthetic session_leases/agents row -- it cannot spawn "
        "a real agent process, let it go idle for 5+ minutes, or make its "
        "watcher hit a real usage limit. That end of agent recovery is only "
        "provable by T-185 running against a live board with real connected "
        "agents, once T-181's hook adapter (still in progress) is the thing "
        "producing the heartbeats this test currently writes by hand."
    )


def test_gap_true_multi_process_concurrency_beyond_in_process_threading():
    pytest.skip(
        "GAP: every concurrency test in this file races real HTTP requests "
        "over real sockets, but from Python threads inside ONE process "
        "sharing one BoardServer/sqlite connection pool -- test_live_http.py's "
        "own claim-race test has the identical shape and scope. Genuine "
        "multi-PROCESS contention (two separate `tickets.py`-launched agent "
        "runtimes, or a real master process restarting mid-sweep) is only "
        "provable once T-185 points at an actually-deployed, separately-"
        "processed board."
    )
