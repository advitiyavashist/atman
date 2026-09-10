"""Team isolation: who may send, read, route and run -- over the socket.

Design doc: "Test private channel access, spoofed sender, revoked membership
... Agent sender identity comes from its credential, never from request body
fields. Revocation cancels pending unauthorized deliveries."
"""

from __future__ import annotations

import pytest

from .live_board import Agent, FakeLauncher, Http, rid


def _agent_version(board, agent_id):
    return next(a for a in board.operator.get("/agents").body["items"]
                if a["id"] == agent_id)["version"]


def _revoke_agent(board, agent: Agent):
    r = board.operator.delete("/agents/{}/session-lease".format(agent.agent_id), {
        "request_id": rid(), "expected_version": _agent_version(board, agent.agent_id),
        "note": "T-190 revocation"})
    assert r.status == 200, r.body
    return r.body


# ---------------------------------------------------------------- identity

@pytest.mark.parametrize("field", ["author", "sender", "actor", "from"])
def test_a_body_that_asserts_an_author_is_refused_on_both_write_routes(board, field):
    agent = board.enroll("claude-a")
    general = board.channel("general")
    spoof = {field: {"type": "agent", "id": agent.agent_id}}
    denied = board.say(board.operator, general["id"], "as the agent", **spoof)
    assert denied.status == 403 and denied.code() == "sender_identity_rejected"

    source = board.say(board.operator, general["id"], "real").body["message"]
    body = {"request_id": rid(), "outcome": "x",
            "routing": {"mode": "direct", "agent_id": agent.agent_id},
            "ticket": {"new_ticket": {"title": "x"}}}
    body.update(spoof)
    denied = board.operator.post("/messages/{}/task".format(source["id"]), body)
    assert denied.status == 403 and denied.code() == "sender_identity_rejected"
    # Nothing was written: no delivery, no wake job, no draft ticket.
    assert board.store.conn.execute(
        "SELECT COUNT(*) FROM wake_jobs").fetchone()[0] == 0


def test_an_agents_message_is_attributed_to_its_credential(board):
    agent = board.enroll("claude-a")
    general = board.channel("general")
    sent = board.say(agent.http, general["id"], "I am here")
    assert sent.status == 201, sent.body
    assert sent.body["message"]["author"]["type"] == "agent"
    assert sent.body["message"]["author"]["id"] == agent.agent_id


# -------------------------------------------------------- private channels

def test_a_private_channel_is_invisible_and_unusable_to_a_non_member(board):
    insider = board.enroll("insider")
    outsider = board.enroll("outsider")
    private = board.channel("secret", "private")
    board.operator.post("/channels/{}/members".format(private["id"]),
                        {"request_id": rid(), "member_id": insider.member_id})
    source = board.say(board.operator, private["id"], "insiders only").body["message"]

    rail = outsider.http.get("/channels").body["items"]
    assert private["id"] not in {c["id"] for c in rail}
    read = outsider.http.get("/messages", "channel_id=" + private["id"])
    assert read.status == 403 and read.code() == "not_channel_member"
    post = board.say(outsider.http, private["id"], "let me in")
    assert post.status == 403 and post.code() == "not_channel_member"
    # A task inherits the conversation's ACL: an outsider cannot turn a message
    # it may not read into work for the insider.
    task = board.task(outsider.http, source["id"], insider.agent_id)
    assert task.status == 403 and task.code() == "not_channel_member"
    receipts = outsider.http.get("/messages/{}/deliveries".format(source["id"]))
    assert receipts.status == 403

    # And the insider can do all of it.
    assert board.say(insider.http, private["id"], "hi").status == 201
    assert board.task(board.operator, source["id"], insider.agent_id).status == 201


@pytest.mark.xfail(strict=True, reason=(
    "F-14 (T-493): send_task applies the conversation ACL to the sender only. An "
    "agent that is a member of a private channel can task a non-member: 201, "
    "wake job minted, outsider can claim. GET /messages on that channel stays "
    "403 for the outsider. OpenAPI names not_channel_member for READ only."))
def test_a_private_channel_task_cannot_dispatch_a_non_member(board):
    insider = board.enroll("insider")
    outsider = board.enroll("outsider")
    private = board.channel("secret", "private")
    added = board.operator.post("/channels/{}/members".format(private["id"]),
                               {"request_id": rid(), "member_id": insider.member_id})
    assert added.status == 201, added.body
    source = board.say(board.operator, private["id"], "insiders only").body["message"]
    task = board.task(insider.http, source["id"], outsider.agent_id)
    assert task.status in (403, 422)
    assert task.code() in ("not_channel_member", "recipient_not_channel_member")
    assert board.store.conn.execute(
        "SELECT COUNT(*) FROM wake_jobs").fetchone()[0] == 0
    read = outsider.http.get("/messages", "channel_id=" + private["id"])
    assert read.status == 403 and read.code() == "not_channel_member"


def test_a_dm_is_readable_by_exactly_its_two_participants(board):
    a, b, c = board.enroll("a"), board.enroll("b"), board.enroll("c")
    dm = board.dm(a.member_id, b.member_id)
    assert board.say(a.http, dm["id"], "just us").status == 201
    assert board.messages(dm["id"], b.http)["items"][0]["body"] == "just us"
    denied = c.http.get("/messages", "channel_id=" + dm["id"])
    assert denied.status == 403 and denied.code() == "not_channel_member"
    # The operator who minted the DM is not in it either.
    denied = board.operator.get("/messages", "channel_id=" + dm["id"])
    assert denied.status == 403 and denied.code() == "not_channel_member"


# ---------------------------------------------------------- cross-project

def test_another_projects_credential_cannot_reach_this_projects_conversation(board):
    """Two projects on one board. Project B's agent, presenting its own valid
    token, is refused everywhere in project A -- and project A's operator
    cannot route a task at project B's agent."""
    agent_a = board.enroll("a-worker")
    general = board.channel("general")
    source = board.say(board.operator, general["id"], "A talk").body["message"]

    other = board.store.create_project("Other")
    other_session = board.board.bootstrap_operator(other["id"], "OtherOp")
    other_op = Http(board.base_url, other["id"], cookie=other_session["session_token"],
                    csrf=other_session["csrf_token"])
    created = other_op.post("/enrollments", {
        "request_id": rid(), "agent_name": "b-worker", "role": "backend",
        "connection_mode": "managed"})
    assert created.status == 201, created.body
    exchanged = Http(board.base_url, other["id"]).post("/sessions", {
        "request_id": rid(), "code": created.body["code"],
        "session_id": "ses_" + rid().replace("-", "")[:12]})
    assert exchanged.status == 201, exchanged.body
    b_token = exchanged.body["token"]
    b_agent_id = exchanged.body["agent"]["id"]

    # B's token aimed at A's project id: the credential does not belong there.
    intruder = Http(board.base_url, board.project_id, token=b_token)
    for attempt in (intruder.get("/channels"),
                    intruder.get("/messages", "channel_id=" + general["id"]),
                    board.say(intruder, general["id"], "hello from B"),
                    intruder.get("/messages/{}/deliveries".format(source["id"]))):
        assert attempt.status in (401, 403), attempt.body
    # A's operator cannot make A's conversation into B's agent's work.
    misrouted = board.task(board.operator, source["id"], b_agent_id)
    assert misrouted.status == 403 and misrouted.code() == "forbidden_scope"
    # And B's runner, registering in its own project, sees none of A's jobs.
    assert board.task(board.operator, source["id"], agent_a.agent_id).status == 201
    b_runner = Http(board.base_url, other["id"], token=b_token)
    reg = b_runner.post("/runners/register", {
        "request_id": rid(), "runner_id": "rnr_" + rid().replace("-", "")[:12],
        "agent_id": b_agent_id, "allowlisted_worktree": "/tmp/b"})
    assert reg.status == 200, reg.body
    jobs = b_runner.get("/runners/jobs", "runner_id={}&wait_seconds=0".format(reg.body["runner_id"]))
    assert jobs.status == 200 and jobs.body["items"] == []


# --------------------------------------------------------------- revocation

def test_a_revoked_member_can_no_longer_read_or_send(board):
    agent = board.enroll("claude-a")
    general = board.channel("general")
    assert board.say(agent.http, general["id"], "before").status == 201
    member = board.store.get_member(board.project_id, agent.member_id)
    board.store.revoke_member(board.project_id, agent.member_id,
                              expected_version=member["version"])

    for attempt in (board.say(agent.http, general["id"], "after"),
                    agent.http.get("/messages", "channel_id=" + general["id"]),
                    agent.http.get("/channels")):
        assert attempt.status == 403 and attempt.code() == "membership_revoked", attempt.body


def test_revoking_the_session_lease_stops_the_runner_and_the_run_never_starts(board):
    """The operator revokes the agent while a task is pending. The runner's
    token dies with the lease: it cannot lease the job, cannot report a run,
    and nothing reaches `started`."""
    agent = board.enroll("claude-a")
    general = board.channel("general")
    source = board.say(board.operator, general["id"], "do it").body["message"]
    task = board.task(board.operator, source["id"], agent.agent_id)
    assert task.status == 201, task.body
    task = task.body

    sup = board.supervisor(agent)
    sup.register()
    _revoke_agent(board, agent)

    from ticket_board.runners.client import ApiError
    with pytest.raises(ApiError) as refused:
        sup.poll_once(wait_seconds=0)
    assert refused.value.status == 401
    run_id = "run_" + task["wake_job"]["id"][4:]
    with pytest.raises(ApiError):
        board.runner_client(agent).run_event(run_id, "starting", expected_version=1,
                                             session_id=agent.session_id)
    assert board.store.conn.execute(
        "SELECT COUNT(*) FROM runs WHERE recipient_agent_id = ? AND state <> 'pending'",
        (agent.agent_id,)).fetchone()[0] == 0


@pytest.mark.xfail(strict=True, reason=(
    "F-7: revocation does not cancel the pending dispatch. After the operator "
    "revokes an agent, its wake job stays `pending` and its delivery stays "
    "`delivered` indefinitely; nothing marks them blocked/canceled. The design "
    "says revocation cancels pending unauthorized deliveries. (The store's "
    "revoke_member does block `sent`/`queued` deliveries, but a dispatched "
    "task is `delivered`, and no API route reaches revoke_member at all.)"))
def test_revocation_cancels_the_pending_dispatch(board):
    agent = board.enroll("claude-a")
    general = board.channel("general")
    source = board.say(board.operator, general["id"], "do it").body["message"]
    task = board.task(board.operator, source["id"], agent.agent_id).body
    _revoke_agent(board, agent)

    assert board.wake_job(task["wake_job"]["id"])["state"] in ("canceled", "failed")
    receipt = board.deliveries(task["message"]["id"])[0]
    assert receipt["state"] in ("blocked", "canceled")


@pytest.mark.xfail(strict=True, reason=(
    "F-15 (T-493): a task whose existing_ticket_id is already claimed by another "
    "agent still 201s with a delivered receipt. _task_ticket is an unscoped "
    "get_ticket; the supervisor's claim refuses and the run fails, so the "
    "operator only learns from a failed run. Owner must stay unchanged."))
def test_a_task_on_another_agents_claimed_ticket_is_refused_at_send(board):
    owner = board.enroll("owner", max_active_tickets=2)
    other = board.enroll("other")
    ticket = board.ticket("already claimed")
    claimed = owner.http.post("/tickets/{}/claim".format(ticket["id"]), {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": owner.session_id})
    assert claimed.status in (200, 201), claimed.body
    general = board.channel("general")
    source = board.say(board.operator, general["id"], "steal?").body["message"]
    task = board.task(board.operator, source["id"], other.agent_id,
                       ticket={"existing_ticket_id": ticket["id"]})
    assert task.status in (409, 422, 403)
    assert task.body.get("wake_job") in (None, {})
    launcher = FakeLauncher()
    outcomes = board.supervisor(other, launcher=launcher).run_forever(
        wait_seconds=0, max_polls=1)
    assert outcomes == []
    assert launcher.specs == []
    detail = owner.http.get("/tickets/" + ticket["id"]).body
    assert detail["ticket"]["owner"] == owner.agent_id
