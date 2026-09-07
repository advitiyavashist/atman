"""Members, channels, messages and the outbox, over the real router.

Every response here is checked against the frozen contract rather than against
what this server happens to send, because the console lane consumes the
contract and not this file. The ACL tests are the point of the module: the
interesting failure for messaging is never "the route 500s", it is "the route
returns something the caller should not have been able to read".
"""

import uuid

import pytest

from api_client import Client, rid
from api_spec import check

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


# ------------------------------------------------------------------ helpers

def _operator_member(server, operator, project):
    """The `mem_` id the bootstrap operator acts as."""
    items = operator.get("/members").json()["items"]
    return next(m for m in items if m["kind"] == "human")


def _second_operator(server, project, role="member"):
    """Another human in the same project, with their own session."""
    session = server.bootstrap_operator(project["id"], "Second", role=role)
    return Client(server, project_id=project["id"],
                  cookie=session["session_token"], csrf=session["csrf_token"])


def _member_of(client, kind="human", name=None):
    for m in client.get("/members").json()["items"]:
        if m["kind"] == kind and (name is None or m["display_name"] == name):
            return m
    raise AssertionError("no {} member named {}".format(kind, name))


def _agent(server, operator, project, name, *, connection_mode="managed"):
    created = operator.post("/enrollments", {
        "request_id": rid(), "agent_name": name, "role": "backend",
        "connection_mode": connection_mode})
    assert created.status == 201, created.json()
    session_id = "ses_" + uuid.uuid4().hex[:8]
    exchanged = Client(server, project_id=project["id"]).post("/sessions", {
        "request_id": rid(), "code": created.json()["code"],
        "session_id": session_id})
    assert exchanged.status == 201, exchanged.json()
    payload = exchanged.json()
    return {"agent_id": payload["agent"]["id"], "session_id": session_id,
            "client": Client(server, project_id=project["id"],
                             token=payload["token"], origin=None)}


def _channel(operator, name, visibility="public", **extra):
    body = {"request_id": rid(), "name": name, "visibility": visibility}
    body.update(extra)
    created = operator.post("/channels", body)
    assert created.status == 201, created.json()
    return created.json()


def _say(client, channel_id, text="hello", **extra):
    body = {"request_id": rid(), "channel_id": channel_id, "body": text,
            "intent": "message"}
    body.update(extra)
    return client.post("/messages", body)


# ------------------------------------------------------------------ members

def test_members_list_validates_and_adopts_both_identity_kinds(
        server, operator, project):
    """An operator and an enrolled agent are both members, without joining.

    Neither one ever calls a "create member" route -- there isn't one in the
    contract. The operator's row is adopted from its `mem_`-shaped id and the
    agent's is created at enrollment, so the Members list and a mention picker
    can show an agent before it has said anything.
    """
    _agent(server, operator, project, "backend-1")
    response = operator.get("/members")
    assert response.status == 200
    check("MemberListResponse", response.json(), label="GET /members")

    kinds = {m["kind"]: m for m in response.json()["items"]}
    assert set(kinds) == {"human", "agent"}
    assert kinds["agent"]["agent_id"] is not None
    assert kinds["human"]["agent_id"] is None
    assert kinds["human"]["id"].startswith("mem_")


def test_members_are_project_scoped(server, operator, project):
    other = server.store.create_project("Other")
    other_session = server.bootstrap_operator(other["id"], "Elsewhere")
    other_client = Client(server, project_id=other["id"],
                          cookie=other_session["session_token"],
                          csrf=other_session["csrf_token"])
    _agent(server, operator, project, "backend-1")

    names = {m["display_name"] for m in other_client.get("/members").json()["items"]}
    assert "backend-1" not in names
    assert names == {"Elsewhere"}


# -------------------------------------------------------------- invitations

def test_an_invitation_round_trip_creates_a_member_and_a_session(
        server, operator, project):
    """The contract's join flow: owner invites, human exchanges, session set.

    The 201 body is a bare `Member` because the contract says the session
    arrives as `Set-Cookie` and that no token appears in a body or a URL.
    """
    created = operator.post("/invitations", {"request_id": rid(), "role": "member"})
    assert created.status == 201, created.json()
    check("CreateInvitationResponse", created.json(), label="POST /invitations")
    code = created.json()["code"]

    joined = Client(server, project_id=project["id"]).post("/invitations/exchange", {
        "request_id": rid(), "code": code, "display_name": "Dana"})
    assert joined.status == 201, joined.json()
    check("Member", joined.json(), label="POST /invitations/exchange")
    assert joined.json()["display_name"] == "Dana"
    assert joined.json()["role"] == "member"

    cookie = joined.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "tb_session=" in cookie
    # The secret is in the header and nowhere else.
    assert cookie.split("tb_session=")[1].split(";")[0] not in str(joined.json())

    # And the session actually works.
    token = cookie.split("tb_session=")[1].split(";")[0]
    dana = Client(server, project_id=project["id"], cookie=token,
                  csrf=joined.headers["X-CSRF-Token"])
    assert dana.get("/members").status == 200


def test_an_invite_code_is_single_use(server, operator, project):
    code = operator.post("/invitations", {
        "request_id": rid(), "role": "member"}).json()["code"]
    anonymous = Client(server, project_id=project["id"])
    first = anonymous.post("/invitations/exchange", {
        "request_id": rid(), "code": code, "display_name": "Dana"})
    assert first.status == 201
    second = anonymous.post("/invitations/exchange", {
        "request_id": rid(), "code": code, "display_name": "Impostor"})
    assert second.status == 422
    assert second.json()["error"]["code"] == "enrollment_code_invalid"


def test_a_spent_and_an_unknown_invite_code_are_indistinguishable(
        server, operator, project):
    """A stranger with a guessed code must not learn that it exists."""
    code = operator.post("/invitations", {
        "request_id": rid(), "role": "member"}).json()["code"]
    anonymous = Client(server, project_id=project["id"])
    anonymous.post("/invitations/exchange", {
        "request_id": rid(), "code": code, "display_name": "Dana"})

    spent = anonymous.post("/invitations/exchange", {
        "request_id": rid(), "code": code, "display_name": "X"}).json()["error"]
    unknown = anonymous.post("/invitations/exchange", {
        "request_id": rid(), "code": "n" * 24, "display_name": "X"}).json()["error"]
    assert spent["code"] == unknown["code"]
    assert spent["message"] == unknown["message"]


def test_an_invite_code_cannot_be_redeemed_in_another_project(
        server, operator, project):
    other = server.store.create_project("Other")
    server.bootstrap_operator(other["id"], "Elsewhere")
    code = operator.post("/invitations", {
        "request_id": rid(), "role": "member"}).json()["code"]

    stolen = Client(server, project_id=other["id"]).post("/invitations/exchange", {
        "request_id": rid(), "code": code, "display_name": "Impostor"})
    assert stolen.status == 422
    assert stolen.json()["error"]["code"] == "enrollment_code_invalid"


def test_an_expired_invite_code_is_refused(server, operator, project):
    code = operator.post("/invitations", {
        "request_id": rid(), "role": "member", "ttl_seconds": 60}).json()["code"]
    server.store.conn.execute(
        "UPDATE invitations SET expires_at = '2000-01-01T00:00:00Z'")
    server.store.conn.commit()

    response = Client(server, project_id=project["id"]).post(
        "/invitations/exchange",
        {"request_id": rid(), "code": code, "display_name": "Dana"})
    assert response.status == 422
    assert response.json()["error"]["code"] == "enrollment_code_expired"


def test_an_agent_cannot_issue_an_invitation(server, operator, project):
    """`security: [operatorSession]` in the contract, enforced at the router."""
    agent = _agent(server, operator, project, "backend-1")
    response = agent["client"].post("/invitations", {
        "request_id": rid(), "role": "owner"})
    assert response.status == 403
    assert response.json()["error"]["code"] == "agent_token_insufficient"


def test_a_plain_member_cannot_issue_an_invitation(server, operator, project):
    """Operator-session is necessary, not sufficient: the role still decides.

    Without this, the first person invited as a `viewer` could invite an owner.
    """
    viewer = _second_operator(server, project, role="viewer")
    response = viewer.post("/invitations", {"request_id": rid(), "role": "owner"})
    assert response.status == 403
    assert response.json()["error"]["code"] == "forbidden_scope"


def test_replaying_an_invitation_is_refused_not_reissued(server, operator, project):
    """A retry cannot honestly reissue the code, so it is refused (T-286).

    `store.create_invitation`'s idempotency record cannot hold the real code
    -- only its hash lives in `credentials` -- so before this fix a replay
    served a placeholder that was never registered: 201, well-formed, and a
    guaranteed 422 if anyone tried to redeem it. This drives the repro all
    the way to redemption, not just the response shape, because a schema
    check alone cannot tell a real code from a dud one.
    """
    key = rid()
    first = operator.post("/invitations", {"request_id": key, "role": "member"})
    assert first.status == 201, first.json()
    check("CreateInvitationResponse", first.json(), label="POST /invitations")
    real_code = first.json()["code"]

    replay = operator.post("/invitations", {"request_id": key, "role": "member"})
    assert replay.status == 409, replay.json()
    check("ErrorResponse", replay.json(), label="POST /invitations (replay)")
    assert replay.json()["error"]["code"] == "request_id_reused"
    # The refusal must not itself mint or register anything the real code's
    # redemption could collide with.
    assert "code" not in replay.json()

    # The ORIGINAL code is unaffected by the refused replay and still redeems.
    anonymous = Client(server, project_id=project["id"])
    joined = anonymous.post("/invitations/exchange", {
        "request_id": rid(), "code": real_code, "display_name": "Dana"})
    assert joined.status == 201, joined.json()
    assert joined.json()["display_name"] == "Dana"


def test_a_third_identical_invitation_request_is_also_refused(operator):
    """The refusal is stable across repeats, not a one-shot fluke."""
    key = rid()
    operator.post("/invitations", {"request_id": key, "role": "member"})
    for _ in range(2):
        replay = operator.post("/invitations", {"request_id": key, "role": "member"})
        assert replay.status == 409, replay.json()
        assert replay.json()["error"]["code"] == "request_id_reused"


# ----------------------------------------------------------------- channels

def test_channel_creation_and_listing_validate(operator):
    created = operator.post("/channels", {
        "request_id": rid(), "name": "general", "visibility": "public",
        "topic": "Everything else"})
    assert created.status == 201, created.json()
    check("Channel", created.json(), label="POST /channels")

    listed = operator.get("/channels")
    assert listed.status == 200
    check("ChannelListResponse", listed.json(), label="GET /channels")
    assert [c["name"] for c in listed.json()["items"]] == ["general"]


def test_an_empty_channel_rail_carries_an_empty_state(operator):
    listed = operator.get("/channels")
    check("ChannelListResponse", listed.json(), label="GET /channels (empty)")
    assert listed.json()["items"] == []
    assert "empty_state" in listed.json()


@pytest.mark.parametrize("name", ["General", "has space", "-leading", "", "a" * 61])
def test_a_channel_name_outside_the_frozen_pattern_is_400(operator, name):
    response = operator.post("/channels", {
        "request_id": rid(), "name": name, "visibility": "public"})
    assert response.status == 400
    assert response.json()["error"]["details"]["rejected_fields"] == ["name"]


def test_a_private_channel_is_absent_from_a_non_members_rail(
        server, operator, project):
    """"Absent from the list, not returned with a flag" -- the contract's words.

    A flag would still tell an outsider the channel exists and what it is
    called, which for a private channel is most of what is private about it.
    """
    _channel(operator, "general", "public")
    private = _channel(operator, "secret", "private")

    outsider = _second_operator(server, project)
    names = {c["name"] for c in outsider.get("/channels").json()["items"]}
    assert names == {"general"}
    assert private["name"] not in names


def test_the_creator_of_a_private_channel_can_read_it(operator):
    """Otherwise the author of a private channel is locked out of it."""
    private = _channel(operator, "secret", "private")
    assert {c["name"] for c in operator.get("/channels").json()["items"]} == {"secret"}
    assert _say(operator, private["id"]).status == 201


def test_an_owner_can_add_a_member_to_a_private_channel(
        server, operator, project):
    private = _channel(operator, "secret", "private")
    outsider = _second_operator(server, project)
    guest = _member_of(operator, "human", "Second")

    added = operator.post("/channels/{}/members".format(private["id"]), {
        "request_id": rid(), "member_id": guest["id"]})
    assert added.status == 201, added.json()
    check("ChannelMember", added.json(), label="POST /channels/{id}/members")
    assert {c["name"] for c in outsider.get("/channels").json()["items"]} == {"secret"}


def test_a_non_admin_cannot_add_themselves_to_a_private_channel(
        server, operator, project):
    """On a private channel the membership list *is* the ACL.

    If any member could add themselves, "private" would mean "ask nicely".
    """
    private = _channel(operator, "secret", "private")
    outsider = _second_operator(server, project)
    guest = _member_of(operator, "human", "Second")

    response = outsider.post("/channels/{}/members".format(private["id"]), {
        "request_id": rid(), "member_id": guest["id"]})
    assert response.status == 403
    assert response.json()["error"]["code"] == "forbidden_scope"


def test_adding_a_member_from_another_project_is_refused(
        server, operator, project):
    other = server.store.create_project("Other")
    other_session = server.bootstrap_operator(other["id"], "Elsewhere")
    other_client = Client(server, project_id=other["id"],
                          cookie=other_session["session_token"],
                          csrf=other_session["csrf_token"])
    stranger = _member_of(other_client, "human", "Elsewhere")
    channel = _channel(operator, "general", "public")

    response = operator.post("/channels/{}/members".format(channel["id"]), {
        "request_id": rid(), "member_id": stranger["id"]})
    assert response.status in (403, 404)


# ----------------------------------------------------------------- messages

def test_sending_and_listing_messages_validate(operator):
    channel = _channel(operator, "general")
    sent = _say(operator, channel["id"], "the first thing")
    assert sent.status == 201, sent.json()
    check("SendMessageResponse", sent.json(), label="POST /messages")

    listed = operator.get("/messages", query="channel_id=" + channel["id"])
    assert listed.status == 200
    check("MessageListResponse", listed.json(), label="GET /messages")
    assert [m["body"] for m in listed.json()["items"]] == ["the first thing"]


def test_the_author_comes_from_the_credential(server, operator, project):
    channel = _channel(operator, "general")
    agent = _agent(server, operator, project, "backend-1")
    agent_message = _say(agent["client"], channel["id"], "from the agent")

    assert agent_message.json()["message"]["author"]["type"] == "agent"
    assert agent_message.json()["message"]["author"]["id"] == agent["agent_id"]
    human_message = _say(operator, channel["id"], "from the human")
    assert human_message.json()["message"]["author"]["type"] == "human"


@pytest.mark.parametrize("field", ["author", "sender", "actor", "from"])
def test_an_author_in_the_body_is_sender_identity_rejected(operator, field):
    """403 with the contract's own code, not the generic 400 for stray fields.

    `validate.check_body` would refuse these as malformed. The contract names
    `sender_identity_rejected` for this route specifically, and the two say
    different things: one means "I have no such field", this means "the author
    is not yours to assert".
    """
    channel = _channel(operator, "general")
    response = operator.post("/messages", {
        "request_id": rid(), "channel_id": channel["id"], "body": "spoofed",
        "intent": "message", field: {"type": "human", "id": "mem_00000000"}})
    assert response.status == 403
    assert response.json()["error"]["code"] == "sender_identity_rejected"


def test_reading_a_private_channel_you_are_not_in_is_not_channel_member(
        server, operator, project):
    private = _channel(operator, "secret", "private")
    _say(operator, private["id"], "internal")
    outsider = _second_operator(server, project)

    response = outsider.get("/messages", query="channel_id=" + private["id"])
    assert response.status == 403
    assert response.json()["error"]["code"] == "not_channel_member"


def test_a_revoked_member_is_membership_revoked(server, operator, project):
    channel = _channel(operator, "general")
    _say(operator, channel["id"], "hello")
    outsider = _second_operator(server, project)
    guest = _member_of(operator, "human", "Second")
    server.store.revoke_member(project["id"], guest["id"],
                               expected_version=guest["version"])

    response = outsider.get("/messages", query="channel_id=" + channel["id"])
    assert response.status == 403
    assert response.json()["error"]["code"] == "membership_revoked"


def test_posting_into_a_private_channel_you_are_not_in_is_refused(
        server, operator, project):
    private = _channel(operator, "secret", "private")
    outsider = _second_operator(server, project)
    response = _say(outsider, private["id"], "I should not be here")
    assert response.status == 403
    assert response.json()["error"]["code"] == "not_channel_member"


def test_listing_messages_requires_a_channel_id(operator):
    """`channel_id` is `required: true` on the query, so its absence is a 400."""
    response = operator.get("/messages")
    assert response.status == 400
    assert response.json()["error"]["details"]["missing_fields"] == ["channel_id"]


def test_a_message_cannot_be_read_across_projects(server, operator, project):
    channel = _channel(operator, "general")
    _say(operator, channel["id"], "alpha only")
    other = server.store.create_project("Other")
    other_session = server.bootstrap_operator(other["id"], "Elsewhere")
    other_client = Client(server, project_id=other["id"],
                          cookie=other_session["session_token"],
                          csrf=other_session["csrf_token"])

    response = other_client.get("/messages", query="channel_id=" + channel["id"])
    assert response.status in (403, 404)


def test_a_mention_creates_a_delivery_for_the_named_agent(
        server, operator, project):
    """A mention wakes the named agent; ordinary conversation does not.

    The delivery row is the evidence -- an unmentioned agent gets none, which
    is what stops every message from launching every agent in the project.
    """
    channel = _channel(operator, "general")
    mentioned = _agent(server, operator, project, "backend-1")
    _agent(server, operator, project, "backend-2")
    member = next(m for m in operator.get("/members").json()["items"]
                  if m["agent_id"] == mentioned["agent_id"])

    sent = _say(operator, channel["id"], "@backend-1 please look",
                mentions=[member["id"]])
    assert sent.status == 201, sent.json()
    recipients = [d["recipient_agent_id"] for d in sent.json()["deliveries"]]
    assert recipients == [mentioned["agent_id"]]


def test_an_ordinary_message_wakes_nobody(server, operator, project):
    channel = _channel(operator, "general")
    _agent(server, operator, project, "backend-1")
    sent = _say(operator, channel["id"], "just talking")
    assert sent.json()["deliveries"] == []


def test_the_message_and_its_outbox_are_one_transaction(
        server, operator, project, monkeypatch):
    """A message persisted without its outbox rows is the bug the shape prevents.

    Forcing the delivery insert to fail must leave *no* message behind, not a
    message nobody will ever be woken for.
    """
    channel = _channel(operator, "general")
    agent = _agent(server, operator, project, "backend-1")
    member = next(m for m in operator.get("/members").json()["items"]
                  if m["agent_id"] == agent["agent_id"])

    def boom(*args, **kwargs):
        raise RuntimeError("delivery insert failed")

    monkeypatch.setattr(server.store, "_insert_delivery", boom)
    response = _say(operator, channel["id"], "should not survive",
                    mentions=[member["id"]])
    assert response.status == 500

    monkeypatch.undo()
    listed = operator.get("/messages", query="channel_id=" + channel["id"])
    assert [m["body"] for m in listed.json()["items"]] == []


def test_deliveries_for_a_message_validate(server, operator, project):
    channel = _channel(operator, "general")
    agent = _agent(server, operator, project, "backend-1")
    member = next(m for m in operator.get("/members").json()["items"]
                  if m["agent_id"] == agent["agent_id"])
    sent = _say(operator, channel["id"], "@backend-1", mentions=[member["id"]])

    response = operator.get(
        "/messages/{}/deliveries".format(sent.json()["message"]["id"]))
    assert response.status == 200
    check("DeliveryListResponse", response.json(),
          label="GET /messages/{id}/deliveries")
    assert len(response.json()["items"]) == 1


def test_receipts_inherit_the_channels_acl(server, operator, project):
    """Seeing who a message reached requires being able to read the channel."""
    private = _channel(operator, "secret", "private")
    sent = _say(operator, private["id"], "internal")
    outsider = _second_operator(server, project)

    response = outsider.get(
        "/messages/{}/deliveries".format(sent.json()["message"]["id"]))
    assert response.status == 403
    assert response.json()["error"]["code"] == "not_channel_member"


def test_deliveries_for_another_projects_message_are_404(
        server, operator, project):
    channel = _channel(operator, "general")
    sent = _say(operator, channel["id"], "alpha only")
    other = server.store.create_project("Other")
    other_session = server.bootstrap_operator(other["id"], "Elsewhere")
    other_client = Client(server, project_id=other["id"],
                          cookie=other_session["session_token"],
                          csrf=other_session["csrf_token"])

    response = other_client.get(
        "/messages/{}/deliveries".format(sent.json()["message"]["id"]))
    assert response.status == 404
    # Same answer as a message that does not exist: ids stay unprobeable.
    missing = other_client.get("/messages/msg_00000000/deliveries")
    assert response.json()["error"]["message"] == missing.json()["error"]["message"]


# --------------------------------------------------------------- send task

_NO_TICKET = object()
_DEFAULT_TICKET = {"new_ticket": {"title": "Document it", "role": "backend"}}


def _task(client, message_id, agent_id=None, *, mode="direct",
          ticket=_DEFAULT_TICKET, outcome="Document the widget endpoint."):
    """`ticket=_NO_TICKET` omits the key; `ticket={}` really sends `{}`."""
    routing = {"mode": mode}
    if agent_id is not None:
        routing["agent_id"] = agent_id
    body = {"request_id": rid(), "outcome": outcome, "routing": routing}
    if ticket is not _NO_TICKET:
        body["ticket"] = ticket
    return client.post("/messages/{}/task".format(message_id), body)


def test_a_dispatched_task_validates_and_wakes_a_managed_agent(
        server, operator, project):
    channel = _channel(operator, "general")
    agent = _agent(server, operator, project, "backend-1",
                   connection_mode="managed")
    source = _say(operator, channel["id"], "can someone document this?")

    response = _task(operator, source.json()["message"]["id"], agent["agent_id"])
    assert response.status == 201, response.json()
    check("SendTaskResponse", response.json(), label="POST /messages/{id}/task")

    payload = response.json()
    assert payload["message"]["intent"] == "task"
    assert payload["message"]["ticket_id"] == payload["ticket"]["id"]
    assert payload["wake_job"] is not None
    assert payload["wake_job"]["recipient_agent_id"] == agent["agent_id"]
    assert [d["state"] for d in payload["deliveries"]] == ["delivered"]
    assert payload["deliveries"][0]["reason"] is None


def test_a_hook_only_recipient_is_queued_with_manual_resume_required(
        server, operator, project):
    """Not an error: a 201 whose receipt says why nobody was woken.

    A hook-only agent has no runner to wake, so the honest answer is a stored
    task and a reason -- not a failure the operator has to interpret, and not a
    silent success that implies dispatch.
    """
    channel = _channel(operator, "general")
    agent = _agent(server, operator, project, "hook-1",
                   connection_mode="hook_only")
    source = _say(operator, channel["id"], "please do this")

    response = _task(operator, source.json()["message"]["id"], agent["agent_id"])
    assert response.status == 201, response.json()
    check("SendTaskResponse", response.json(), label="task (hook-only)")
    assert response.json()["wake_job"] is None
    delivery = response.json()["deliveries"][0]
    assert delivery["state"] == "queued"
    assert delivery["reason"] == "manual_resume_required"


def test_a_paused_project_queues_the_task_with_project_paused(
        server, operator, project):
    channel = _channel(operator, "general")
    agent = _agent(server, operator, project, "backend-1")
    source = _say(operator, channel["id"], "later please")
    server.store.conn.execute("UPDATE projects SET paused = 1 WHERE id = ?",
                              (project["id"],))
    server.store.conn.commit()

    response = _task(operator, source.json()["message"]["id"], agent["agent_id"])
    assert response.status == 201, response.json()
    check("SendTaskResponse", response.json(), label="task (paused)")
    assert response.json()["wake_job"] is None
    delivery = response.json()["deliveries"][0]
    assert delivery["state"] == "queued"
    assert delivery["reason"] == "project_paused"
    # Pausing must not silently drop the work.
    assert response.json()["ticket"]["id"]


def test_a_task_can_link_an_existing_ticket(server, operator, project, ticket):
    channel = _channel(operator, "general")
    agent = _agent(server, operator, project, "backend-1")
    source = _say(operator, channel["id"], "this one")

    response = _task(operator, source.json()["message"]["id"], agent["agent_id"],
                     ticket={"existing_ticket_id": ticket["id"]})
    assert response.status == 201, response.json()
    assert response.json()["ticket"]["id"] == ticket["id"]


def test_via_master_routes_to_the_channels_designated_master(
        server, operator, project):
    """`via_master` is a redirect to one decider, never a broadcast."""
    master_agent = _agent(server, operator, project, "the-master")
    other = _agent(server, operator, project, "backend-1")
    channel = _channel(operator, "general")
    server.store.conn.execute(
        "UPDATE channels SET designated_master = ? WHERE id = ?",
        (master_agent["agent_id"], channel["id"]))
    server.store.conn.commit()
    source = _say(operator, channel["id"], "somebody take this")

    response = _task(operator, source.json()["message"]["id"], mode="via_master")
    assert response.status == 201, response.json()
    recipients = [d["recipient_agent_id"] for d in response.json()["deliveries"]]
    assert recipients == [master_agent["agent_id"]]
    assert other["agent_id"] not in recipients


def test_via_master_without_a_designated_master_is_refused(operator):
    """Rather than fanning out to everyone, which is what the mode exists to avoid."""
    channel = _channel(operator, "general")
    source = _say(operator, channel["id"], "somebody take this")
    response = _task(operator, source.json()["message"]["id"], mode="via_master")
    assert response.status == 400
    assert "designated master" in response.json()["error"]["message"]


def test_direct_routing_without_an_agent_id_is_refused(operator):
    channel = _channel(operator, "general")
    source = _say(operator, channel["id"], "who?")
    response = _task(operator, source.json()["message"]["id"])
    assert response.status == 400
    assert response.json()["error"]["details"]["missing_fields"] == \
        ["routing.agent_id"]


def test_a_task_cannot_route_to_an_agent_in_another_project(
        server, operator, project):
    other = server.store.create_project("Other")
    other_session = server.bootstrap_operator(other["id"], "Elsewhere")
    other_client = Client(server, project_id=other["id"],
                          cookie=other_session["session_token"],
                          csrf=other_session["csrf_token"])
    stranger = _agent(server, other_client, other, "their-agent")
    channel = _channel(operator, "general")
    source = _say(operator, channel["id"], "hello")

    response = _task(operator, source.json()["message"]["id"],
                     stranger["agent_id"])
    assert response.status == 403
    assert response.json()["error"]["code"] == "forbidden_scope"


def test_a_task_on_a_channel_you_cannot_read_is_refused(
        server, operator, project):
    """A task inherits the conversation's ACL.

    Otherwise an outsider who guessed a message id could create work, and a
    ticket, inside a private channel.
    """
    private = _channel(operator, "secret", "private")
    agent = _agent(server, operator, project, "backend-1")
    source = _say(operator, private["id"], "internal")
    outsider = _second_operator(server, project)

    response = _task(outsider, source.json()["message"]["id"], agent["agent_id"])
    assert response.status == 403
    assert response.json()["error"]["code"] == "not_channel_member"


def test_a_task_body_that_asserts_an_author_is_refused(server, operator, project):
    channel = _channel(operator, "general")
    agent = _agent(server, operator, project, "backend-1")
    source = _say(operator, channel["id"], "hello")

    response = operator.post(
        "/messages/{}/task".format(source.json()["message"]["id"]),
        {"request_id": rid(), "outcome": "do it",
         "routing": {"mode": "direct", "agent_id": agent["agent_id"]},
         "ticket": {"new_ticket": {"title": "t"}},
         "author": {"type": "human", "id": "mem_00000000"}})
    assert response.status == 403
    assert response.json()["error"]["code"] == "sender_identity_rejected"


def test_a_task_ticket_spec_must_name_exactly_one_branch(
        server, operator, project, ticket):
    channel = _channel(operator, "general")
    agent = _agent(server, operator, project, "backend-1")
    source = _say(operator, channel["id"], "hello")
    mid = source.json()["message"]["id"]

    both = _task(operator, mid, agent["agent_id"],
                 ticket={"existing_ticket_id": ticket["id"],
                         "new_ticket": {"title": "t"}})
    assert both.status == 400
    neither = _task(operator, mid, agent["agent_id"], ticket={})
    assert neither.status == 400


def test_a_task_without_a_ticket_is_refused(server, operator, project):
    """`ticket` is absent from the schema's `required`, but the route's own
    description says a task needs "a linked or new ticket", and routing work at
    an agent with nowhere to record progress is not a task. Read as required
    here; recorded for T-224 as a place the prose and the schema disagree.
    """
    channel = _channel(operator, "general")
    agent = _agent(server, operator, project, "backend-1")
    source = _say(operator, channel["id"], "hello")
    response = _task(operator, source.json()["message"]["id"],
                     agent["agent_id"], ticket=_NO_TICKET)
    assert response.status == 400
    assert response.json()["error"]["details"]["rejected_fields"] == ["ticket"]


# ------------------------------------------------------------------ threads

def test_a_reply_lands_in_its_thread_and_the_filter_narrows_to_it(operator):
    """`thread_id` on GET /messages is a filter, not a hint.

    The unfiltered rail is the whole channel; the filtered one is the thread.
    If the filter were ignored the console would render every channel message
    inside whichever thread the reader opened.
    """
    channel = _channel(operator, "general")
    root = _say(operator, channel["id"], "shall we ship it?").json()["message"]
    thread = operator.server.store.create_thread(
        operator.project_id, channel["id"], root["id"])

    reply = _say(operator, channel["id"], "yes", intent="reply",
                 thread_id=thread["id"])
    assert reply.status == 201, reply.json()
    assert reply.json()["message"]["thread_id"] == thread["id"]
    _say(operator, channel["id"], "unrelated aside")

    whole = operator.get("/messages", query="channel_id=" + channel["id"])
    check("MessageListResponse", whole.json(), label="GET /messages (channel)")
    assert len(whole.json()["items"]) == 3

    narrowed = operator.get("/messages", query="channel_id={}&thread_id={}".format(
        channel["id"], thread["id"]))
    check("MessageListResponse", narrowed.json(), label="GET /messages (thread)")
    # The root is in its own thread -- `create_thread` stamps its `thread_id`
    # -- so the thread rail is root + replies, and only the aside is excluded.
    assert [m["body"] for m in narrowed.json()["items"]] == [
        "shall we ship it?", "yes"]
    assert [t["reply_count"] for t in narrowed.json()["threads"]
            if t["id"] == thread["id"]] == [1]


def test_a_reply_cannot_be_smuggled_into_another_channels_thread(operator):
    """The thread is the authority on which channel it belongs to.

    Without this check a reader who can see channel A could plant a message
    into a thread rendered under channel B.
    """
    a = _channel(operator, "general")
    b = _channel(operator, "work")
    root = _say(operator, a["id"], "root").json()["message"]
    thread = operator.server.store.create_thread(
        operator.project_id, a["id"], root["id"])

    response = _say(operator, b["id"], "wrong channel", intent="reply",
                    thread_id=thread["id"])
    assert response.status == 400
    assert response.json()["error"]["details"]["thread_id"] == thread["id"]


def test_an_unknown_thread_is_404_not_a_silently_rootless_message(operator):
    channel = _channel(operator, "general")
    response = _say(operator, channel["id"], "orphan", intent="reply",
                    thread_id="thr_nosuch1")
    assert response.status == 404


# ---------------------------------------------------------------------- DMs

def _dm(server, project, *member_ids, name="dm-operator-backend1"):
    """A DM is a private channel with `kind='dm'`.

    Minted through the store on purpose: `CreateChannelRequest` has no `kind`
    field and is `additionalProperties: false`, so under the frozen contract
    POST /channels can only ever produce `kind='channel'`. See the review
    notes -- the record shape supports DMs, the route surface does not, and
    inventing a route here would be an amendment, not an implementation.
    """
    channel = server.store.create_channel(project["id"], name, "private",
                                          kind="dm")
    for member_id in member_ids:
        server.store.add_channel_member(project["id"], channel["id"], member_id)
    return channel


def test_a_dm_is_readable_by_its_two_members_and_nobody_else(
        server, operator, project):
    mine = _operator_member(server, operator, project)
    other = _second_operator(server, project)
    guest = _member_of(operator, "human", "Second")
    outsider = _second_operator(server, project, role="member")

    dm = _dm(server, project, mine["id"], guest["id"])

    for reader in (operator, other):
        listed = reader.get("/channels")
        check("ChannelListResponse", listed.json(), label="GET /channels (dm)")
        assert [c["kind"] for c in listed.json()["items"]
                if c["id"] == dm["id"]] == ["dm"]
    assert dm["id"] not in {c["id"] for c in outsider.get("/channels").json()["items"]}

    assert _say(operator, dm["id"], "just us").status == 201
    denied = outsider.get("/messages", query="channel_id=" + dm["id"])
    assert denied.status == 403
    assert denied.json()["error"]["code"] == "not_channel_member"


def test_a_dm_cannot_be_minted_through_post_channels(operator):
    """`kind` is not a field of CreateChannelRequest, so it is a rejected key.

    Recorded rather than worked around: the contract has no DM-creation route
    at all, which is a real gap for T-224, not something this lane may close.
    """
    response = operator.post("/channels", {
        "request_id": rid(), "name": "dm-a-b", "visibility": "private",
        "kind": "dm"})
    assert response.status == 400
    assert response.json()["error"]["details"]["rejected_fields"] == ["kind"]


# ----------------------------------------------------------------- replay

def test_a_replayed_send_returns_the_first_message_and_no_second_row(operator):
    """Dedup is by `request_id`, and a retry is a success, not a 409.

    A dropped response is the ordinary case: the client retries the same body
    and must get the same message back rather than posting it twice.
    """
    channel = _channel(operator, "general")
    body = {"request_id": rid(), "channel_id": channel["id"],
            "body": "exactly once", "intent": "message"}

    first = operator.post("/messages", body)
    second = operator.post("/messages", body)
    assert (first.status, second.status) == (201, 201), second.json()
    assert second.json()["message"]["id"] == first.json()["message"]["id"]

    listed = operator.get("/messages", query="channel_id=" + channel["id"])
    assert [m["body"] for m in listed.json()["items"]] == ["exactly once"]


def test_a_replayed_channel_creation_does_not_mint_a_second_channel(operator):
    body = {"request_id": rid(), "name": "general", "visibility": "public"}
    first = operator.post("/channels", body)
    second = operator.post("/channels", body)
    assert (first.status, second.status) == (201, 201), second.json()
    assert second.json()["id"] == first.json()["id"]
    assert len(operator.get("/channels").json()["items"]) == 1


def test_a_replayed_task_wakes_the_agent_once(server, operator, project):
    """The expensive half of dedup: a replay must not spawn a second run.

    A duplicated wake job is a duplicated agent run against the same ticket,
    which is the failure the outbox exists to prevent.
    """
    channel = _channel(operator, "general")
    agent = _agent(server, operator, project, "backend-1")
    source = _say(operator, channel["id"], "can someone document this?")
    message_id = source.json()["message"]["id"]
    body = {"request_id": rid(), "outcome": "Document the widget endpoint.",
            "routing": {"mode": "direct", "agent_id": agent["agent_id"]},
            "ticket": _DEFAULT_TICKET}

    first = operator.post("/messages/{}/task".format(message_id), body)
    second = operator.post("/messages/{}/task".format(message_id), body)
    assert (first.status, second.status) == (201, 201), second.json()
    assert second.json()["message"]["id"] == first.json()["message"]["id"]
    assert second.json()["ticket"]["id"] == first.json()["ticket"]["id"]

    wake_jobs = server.store.conn.execute(
        "SELECT id FROM wake_jobs WHERE project_id = ?", (project["id"],)
    ).fetchall()
    assert len(wake_jobs) == 1


def test_the_same_task_key_aimed_at_another_message_is_a_conflict(
        server, operator, project):
    """Replay is same id *and* same request. A different source message under a
    reused key is the 409 the envelope exists to raise, not a replayed receipt
    for work the caller did not ask for."""
    channel = _channel(operator, "general")
    agent = _agent(server, operator, project, "backend-1")
    first_source = _say(operator, channel["id"], "document this?")
    second_source = _say(operator, channel["id"], "and this?")
    body = {"request_id": rid(), "outcome": "Document the widget endpoint.",
            "routing": {"mode": "direct", "agent_id": agent["agent_id"]},
            "ticket": _DEFAULT_TICKET}

    assert operator.post("/messages/{}/task".format(
        first_source.json()["message"]["id"]), body).status == 201
    reused = operator.post("/messages/{}/task".format(
        second_source.json()["message"]["id"]), body)
    assert reused.status == 409
    assert reused.json()["error"]["code"] == "request_id_reused"


def test_a_replayed_task_does_not_leak_a_second_draft_ticket(
        server, operator, project):
    """The defect the replay check was added for.

    The old handler minted the draft ticket *before* the inner send compared
    bodies, so every retry left an orphan ticket behind and then answered 409.
    """
    channel = _channel(operator, "general")
    agent = _agent(server, operator, project, "backend-1")
    source = _say(operator, channel["id"], "document this?")
    body = {"request_id": rid(), "outcome": "Document the widget endpoint.",
            "routing": {"mode": "direct", "agent_id": agent["agent_id"]},
            "ticket": _DEFAULT_TICKET}
    path = "/messages/{}/task".format(source.json()["message"]["id"])

    operator.post(path, body)
    operator.post(path, body)
    operator.post(path, body)

    listed = operator.get("/tickets").json()["items"]
    assert [t["title"] for t in listed] == ["Document it"]


def test_a_rail_of_same_second_messages_keeps_the_order_they_were_posted_in(
        operator):
    """`created_at` is second-precision, so a busy channel is *all* ties.

    The tiebreak used to be the message id, which is `secrets.choice` random:
    ten messages posted in one second came back in ten different orders, with
    replies above the messages they answered. Insertion order is the only
    honest answer, and this is the shape a chat rail is read in.
    """
    channel = _channel(operator, "general")
    posted = ["m{}".format(i) for i in range(10)]
    for text in posted:
        assert _say(operator, channel["id"], text).status == 201

    for _ in range(3):
        listed = operator.get("/messages", query="channel_id=" + channel["id"])
        assert [m["body"] for m in listed.json()["items"]] == posted
        assert len({m["created_at"] for m in listed.json()["items"]}) <= 2
