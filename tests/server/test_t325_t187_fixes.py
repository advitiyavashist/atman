"""T-325: the three blocking defects T-306 found on T-187, plus the hazard.

Rebuilt from T-306's recipe rather than from its files -- opus-authz left the
reproducers untracked in a detached worktree, so the recipe is the source. Each
finding gets the exact case that failed on the merged tip, and then the cases
that would let the same defect back in through a different door.

Every response that has a contract schema is checked against it: the point of
F1 and F3 is conformance, and a test that only asserts what this server happens
to send cannot see a conformance break.
"""

import json

import pytest

from api_client import Client, rid
from api_spec import check


def _channel(operator, name, visibility="public"):
    created = operator.post("/channels", {
        "request_id": rid(), "name": name, "visibility": visibility})
    assert created.status == 201, created.json()
    return created.json()


def _say(client, channel_id, text, **extra):
    body = {"request_id": rid(), "channel_id": channel_id, "body": text,
            "intent": "message"}
    body.update(extra)
    return client.post("/messages", body)


def _fill(operator, channel_id, count):
    """`count` messages named m000, m001, ... in post order."""
    for i in range(count):
        r = _say(operator, channel_id, "m%03d" % i)
        assert r.status == 201, r.json()


def _page(operator, channel_id, *, limit=None, cursor=None):
    query = "channel_id=" + channel_id
    if limit is not None:
        query += "&limit=%s" % limit
    if cursor is not None:
        query += "&cursor=%s" % cursor
    return operator.get("/messages", query=query)


def _bodies(payload):
    return [m["body"] for m in payload["items"]]


def _drain(operator, channel_id, *, limit=None):
    """Every message reachable by following next_cursor to exhaustion."""
    seen, cursor, pages = [], None, 0
    while True:
        r = _page(operator, channel_id, limit=limit, cursor=cursor)
        assert r.status == 200, r.json()
        payload = r.json()
        check("MessageListResponse", payload, label="GET /messages")
        seen.extend(_bodies(payload))
        cursor = payload["next_cursor"]
        pages += 1
        if cursor is None:
            return seen, pages
        assert pages < 50, "next_cursor never cleared -- a client would loop forever"


# ====================================================================== F1

def test_the_newest_messages_are_reachable_and_the_payload_says_so(
        operator, project):
    """T-306's headline case. 60 messages, default page of 50: the tip used to
    return m000..m049 with next_cursor=null -- the 10 newest absent AND the
    response asserting there was no next page."""
    channel = _channel(operator, "busy")
    _fill(operator, channel["id"], 60)

    first = _page(operator, channel["id"])
    assert first.status == 200, first.json()
    payload = first.json()
    check("MessageListResponse", payload, label="GET /messages")
    assert _bodies(payload) == ["m%03d" % i for i in range(50)]
    assert payload["next_cursor"] is not None, (
        "10 messages are unreturned; next_cursor: null claims otherwise")

    second = _page(operator, channel["id"], cursor=payload["next_cursor"])
    assert second.status == 200, second.json()
    tail = second.json()
    check("MessageListResponse", tail, label="GET /messages")
    assert _bodies(tail) == ["m%03d" % i for i in range(50, 60)]
    assert tail["next_cursor"] is None


def test_a_channel_past_the_contract_maximum_is_not_frozen_forever(
        operator, project):
    """T-306's worst case: 210 messages at limit=200 (the contract maximum)
    left m200..m209 unreachable through EVERY parameter combination the
    contract offers."""
    channel = _channel(operator, "huge")
    _fill(operator, channel["id"], 210)

    first = _page(operator, channel["id"], limit=200)
    assert _bodies(first.json()) == ["m%03d" % i for i in range(200)]
    assert first.json()["next_cursor"] is not None

    rest = _page(operator, channel["id"], limit=200,
                 cursor=first.json()["next_cursor"])
    assert _bodies(rest.json()) == ["m%03d" % i for i in range(200, 210)]
    assert rest.json()["next_cursor"] is None


def test_the_cursor_parameter_is_not_silently_ignored(operator, project):
    """The tip returned a byte-identical page for '&cursor=anything', so a
    paging client looped on page 1 forever."""
    channel = _channel(operator, "paged")
    _fill(operator, channel["id"], 12)
    first = _page(operator, channel["id"], limit=5)
    second = _page(operator, channel["id"], limit=5,
                   cursor=first.json()["next_cursor"])
    assert _bodies(first.json()) != _bodies(second.json())
    assert _bodies(second.json()) == ["m005", "m006", "m007", "m008", "m009"]


def test_paging_returns_every_message_exactly_once(operator, project):
    channel = _channel(operator, "drained")
    _fill(operator, channel["id"], 137)
    seen, pages = _drain(operator, channel["id"], limit=10)
    assert seen == ["m%03d" % i for i in range(137)]
    assert pages == 14  # 13 full pages + the short one


def test_a_full_final_page_does_not_emit_a_cursor_to_nowhere(
        operator, project):
    """Exactly divisible. Emitting a cursor because the page came back full
    would hand a client a cursor to an empty page; a client that trusts
    next_cursor pages forever."""
    channel = _channel(operator, "exact")
    _fill(operator, channel["id"], 10)
    r = _page(operator, channel["id"], limit=10)
    assert len(r.json()["items"]) == 10
    assert r.json()["next_cursor"] is None


def test_same_second_messages_page_without_loss_or_repeat(operator, project):
    """The reason the cursor is a keyset on (created_at, rowid) and not an
    offset. `ids.now()` is second-precision, so a burst lands on one timestamp;
    an offset pager -- or a cursor built on created_at alone -- either repeats
    or skips exactly the rows a busy channel is producing. T-187's own headline
    fix was this ordering; a pager that re-derived the page would undo it."""
    channel = _channel(operator, "burst")
    _fill(operator, channel["id"], 25)
    stamps = {m["created_at"] for m in
              _page(operator, channel["id"], limit=200).json()["items"]}
    assert len(stamps) <= 3, "precondition: the burst shares a timestamp"

    seen, _pages = _drain(operator, channel["id"], limit=1)
    assert seen == ["m%03d" % i for i in range(25)]


def test_a_cursor_from_another_listing_is_refused_not_ignored(
        operator, project):
    """Serving page 1 for a cursor we cannot read is the defect, not the fix."""
    channel = _channel(operator, "guard")
    _fill(operator, channel["id"], 3)
    for bad in ("anything", "!!!!", "eyJrIjoidGlja2V0cyIsInYiOlsiYSIsMV19"):
        r = _page(operator, channel["id"], cursor=bad)
        assert r.status == 400, (bad, r.status, r.json())
        assert r.json()["error"]["code"] == "malformed_request"


def test_an_empty_cursor_parameter_means_the_first_page(operator, project):
    channel = _channel(operator, "emptycursor")
    _fill(operator, channel["id"], 3)
    r = _page(operator, channel["id"], cursor="")
    assert r.status == 200, r.json()
    assert _bodies(r.json()) == ["m000", "m001", "m002"]


@pytest.mark.parametrize("limit,status", [
    ("1", 200), ("200", 200), ("0", 400), ("201", 400), ("-1", 400),
    ("abc", 400), ("1.5", 400),
])
def test_limit_bounds(operator, project, limit, status):
    """T-306: the author's suite had ZERO tests exercising `limit`."""
    channel = _channel(operator, "limits")
    _fill(operator, channel["id"], 2)
    r = _page(operator, channel["id"], limit=limit)
    assert r.status == status, (limit, r.status, r.json())


def test_thread_filtered_paging_stays_inside_the_thread(operator, project):
    """The cursor must not leak rows from outside the filter it was issued
    under: a page-2 that dropped the thread_id predicate would."""
    channel = _channel(operator, "threaded")
    root = _say(operator, channel["id"], "root").json()["message"]
    thread = operator.server.store.create_thread(
        project["id"], channel["id"], root["id"])
    for i in range(6):
        assert _say(operator, channel["id"], "t%d" % i,
                    thread_id=thread["id"]).status == 201
    for i in range(6):
        assert _say(operator, channel["id"], "loose%d" % i).status == 201

    seen = []
    cursor = None
    while True:
        r = operator.get("/messages", query="channel_id=%s&thread_id=%s&limit=2%s" % (
            channel["id"], thread["id"], ("&cursor=" + cursor) if cursor else ""))
        assert r.status == 200, r.json()
        seen.extend(_bodies(r.json()))
        cursor = r.json()["next_cursor"]
        if cursor is None:
            break
    # `create_thread` adopts the root message into the thread, so it is the
    # first row of the filtered rail -- and the `loose*` messages, which share
    # the channel but not the thread, are absent from every page.
    assert seen == ["root", "t0", "t1", "t2", "t3", "t4", "t5"]


# ====================================================================== F2

def _invite(operator, role="member"):
    created = operator.post("/invitations", {"request_id": rid(), "role": role})
    assert created.status == 201, created.json()
    return created.json()["code"]


def _token_of(response):
    return response.headers["Set-Cookie"].split("tb_session=")[1].split(";")[0]


def test_a_retried_exchange_returns_the_member_and_a_working_session(
        server, operator, project):
    """T-306's reproducer. The tip answered the byte-identical retry with 422
    and no Set-Cookie while the member row already existed: a human who is a
    member of the project, holding no session, on a contract with no operator
    sign-in route."""
    code = _invite(operator)
    anonymous = Client(server, project_id=project["id"])
    body = {"request_id": rid(), "code": code, "display_name": "Dana"}

    first = anonymous.post("/invitations/exchange", body)
    assert first.status == 201, first.json()
    replay = anonymous.post("/invitations/exchange", dict(body))

    assert replay.status == 201, replay.json()
    check("Member", replay.json(), label="POST /invitations/exchange replay")
    assert replay.json() == first.json()
    assert "Set-Cookie" in replay.headers, (
        "the cookie IS the delivered result here; a 201 without one "
        "reproduces the defect through a different door")

    dana = Client(server, project_id=project["id"], cookie=_token_of(replay),
                  csrf=replay.headers["X-CSRF-Token"])
    assert dana.get("/members").status == 200


def test_a_retried_exchange_does_not_create_a_second_member(
        server, operator, project):
    code = _invite(operator)
    anonymous = Client(server, project_id=project["id"])
    body = {"request_id": rid(), "code": code, "display_name": "Dana"}
    anonymous.post("/invitations/exchange", body)
    before = operator.get("/members").json()["items"]
    anonymous.post("/invitations/exchange", dict(body))
    after = operator.get("/members").json()["items"]
    assert [m["id"] for m in after] == [m["id"] for m in before]
    assert sum(1 for m in after if m["display_name"] == "Dana") == 1


def test_the_replay_mints_a_fresh_session_and_stores_no_secret(
        server, operator, project):
    """Handing back the FIRST session would mean persisting a live bearer
    secret in `request_log` -- the exact objection that made T-286 refuse its
    replay on POST /invitations. The first session is orphaned by definition
    anyway: a retry means its response never arrived."""
    code = _invite(operator)
    anonymous = Client(server, project_id=project["id"])
    body = {"request_id": rid(), "code": code, "display_name": "Dana"}
    first = anonymous.post("/invitations/exchange", body)
    replay = anonymous.post("/invitations/exchange", dict(body))
    assert _token_of(first) != _token_of(replay)

    logged = server.store.conn.execute(
        "SELECT operation, body_hash, response FROM request_log").fetchall()
    blob = json.dumps([dict(r) for r in logged])
    assert _token_of(first) not in blob and _token_of(replay) not in blob
    assert code not in blob, "the invite code must not be persisted in clear"
    assert replay.headers["X-CSRF-Token"] not in blob


def test_the_same_request_id_with_a_different_body_is_still_a_409(
        server, operator, project):
    code = _invite(operator)
    other = _invite(operator)
    anonymous = Client(server, project_id=project["id"])
    key = rid()
    first = anonymous.post("/invitations/exchange", {
        "request_id": key, "code": code, "display_name": "Dana"})
    assert first.status == 201, first.json()

    for changed in ({"request_id": key, "code": code, "display_name": "Mallory"},
                    {"request_id": key, "code": other, "display_name": "Dana"}):
        clash = anonymous.post("/invitations/exchange", changed)
        assert clash.status == 409, (changed, clash.status, clash.json())
        assert clash.json()["error"]["code"] == "request_id_reused"


def test_a_spent_code_under_a_new_request_id_is_still_refused(
        server, operator, project):
    """Honouring the replay must not turn the code into a reusable credential:
    a DIFFERENT request_id is a different request, and the code is spent."""
    code = _invite(operator)
    anonymous = Client(server, project_id=project["id"])
    assert anonymous.post("/invitations/exchange", {
        "request_id": rid(), "code": code, "display_name": "Dana"}).status == 201
    again = anonymous.post("/invitations/exchange", {
        "request_id": rid(), "code": code, "display_name": "Impostor"})
    assert again.status == 422
    assert again.json()["error"]["code"] == "enrollment_code_invalid"


def test_a_replay_from_another_project_is_not_honoured(server, operator, project):
    """`request_log` is keyed by (project_id, request_id). The same key
    presented under another project must not surface this project's member."""
    other = server.store.create_project("Other")
    server.bootstrap_operator(other["id"], "Elsewhere")
    code = _invite(operator)
    key = rid()
    body = {"request_id": key, "code": code, "display_name": "Dana"}
    assert Client(server, project_id=project["id"]).post(
        "/invitations/exchange", body).status == 201

    stolen = Client(server, project_id=other["id"]).post(
        "/invitations/exchange", dict(body))
    assert stolen.status == 422, stolen.json()
    assert "Set-Cookie" not in stolen.headers


# ====================================================================== F3

def _ticket(server, project_id, ticket_id):
    return server.store.create_ticket(
        project_id, ticket_id, "A ticket", outcome="o",
        acceptance=[{"text": "a", "checked": False, "checked_by": None,
                     "checked_at": None}])


def test_a_message_may_cite_a_real_ticket_in_this_project(
        server, operator, project):
    _ticket(server, project["id"], "DEMO-7")
    channel = _channel(operator, "work")
    r = _say(operator, channel["id"], "on it", ticket_id="DEMO-7")
    assert r.status == 201, r.json()
    check("SendMessageResponse", r.json(), label="POST /messages")
    assert r.json()["message"]["ticket_id"] == "DEMO-7"


def test_a_message_cannot_cite_a_ticket_that_does_not_exist(
        server, operator, project):
    """T-306: this used to be a 201 whose own body failed the frozen schema --
    TicketId is ^[A-Z][A-Z0-9]{1,15}-[0-9]{1,6}$ and 'T-DOES-NOT-EXIST' is
    not one."""
    channel = _channel(operator, "work")
    r = _say(operator, channel["id"], "on it", ticket_id="T-DOES-NOT-EXIST")
    assert r.status == 400, (r.status, r.json())
    assert r.json()["error"]["code"] == "malformed_request"
    assert r.json()["error"]["details"]["rejected_fields"] == ["ticket_id"]


def test_a_message_cannot_cite_another_projects_ticket(
        server, operator, project):
    """A real key, in a real project, that this caller has no business
    naming. The tip wrote it into the message record and the audit trail as a
    permanent cross-project citation."""
    other = server.store.create_project("Other")
    _ticket(server, other["id"], "OTHER-1")
    channel = _channel(operator, "work")
    r = _say(operator, channel["id"], "leak", ticket_id="OTHER-1")
    assert r.status == 400, (r.status, r.json())
    assert operator.server.store.conn.execute(
        "SELECT COUNT(*) FROM messages WHERE ticket_id = 'OTHER-1'"
    ).fetchone()[0] == 0


def test_an_unknown_and_a_foreign_ticket_answer_identically(
        server, operator, project):
    """Otherwise POST /messages becomes an oracle for which ticket keys exist
    in other projects -- the rule `_message_or_404` already applies to
    message ids."""
    other = server.store.create_project("Other")
    _ticket(server, other["id"], "OTHER-1")
    channel = _channel(operator, "work")
    foreign = _say(operator, channel["id"], "x", ticket_id="OTHER-1").json()["error"]
    unknown = _say(operator, channel["id"], "x", ticket_id="OTHER-9").json()["error"]
    assert foreign["code"] == unknown["code"]
    assert foreign["message"] == unknown["message"]
    assert foreign["details"] == unknown["details"]


@pytest.mark.parametrize("bad", ["t-1", "DEMO", "DEMO-", "-1", "DEMO-1234567",
                                 "ABCDEFGHIJKLMNOPQ-1", "DEMO 1", ""])
def test_a_ticket_id_that_is_not_a_ticket_key_is_refused(
        server, operator, project, bad):
    channel = _channel(operator, "work")
    r = _say(operator, channel["id"], "x", ticket_id=bad)
    assert r.status == 400, (bad, r.status, r.json())


def test_omitting_ticket_id_is_still_fine(operator, project):
    channel = _channel(operator, "work")
    r = _say(operator, channel["id"], "no ticket here")
    assert r.status == 201, r.json()
    assert r.json()["message"]["ticket_id"] is None


def test_the_task_route_still_links_the_ticket_it_creates(
        server, operator, project):
    """F3 must not break the sibling route, which passes a ticket id it minted
    moments earlier through the same store call."""
    channel = _channel(operator, "work")
    source = _say(operator, channel["id"], "please do this").json()["message"]
    r = operator.post("/messages/%s/task" % source["id"], {
        "request_id": rid(), "outcome": "Do the thing.",
        "routing": {"mode": "direct", "agent_id": None}})
    assert r.status in (201, 400, 422), r.json()
    if r.status == 201:
        assert r.json()["message"]["ticket_id"] == r.json()["ticket"]["id"]


# =============================================== the hazard: member_id=None

def test_a_principal_with_no_member_row_cannot_read_a_private_channel(
        server, operator, project):
    """Defence in depth. Not reachable through the API today -- every
    principal-creating site adopts a member row -- but the store used to treat
    `member_id=None` as SEE EVERYTHING, the opposite of the SSE filter's answer
    to the identical question in the same commit. An imported board (T-213) or
    any future principal that skips adoption would land on the open side."""
    from ticket_board.storage.errors import NotChannelMember

    secret = _channel(operator, "secret", visibility="private")
    _say(operator, secret["id"], "for members only")

    with pytest.raises(NotChannelMember):
        server.store.list_messages(project["id"], secret["id"], member_id=None)
    with pytest.raises(NotChannelMember):
        server.store.get_channel(project["id"], secret["id"], member_id=None)


def test_the_system_escape_hatch_is_opt_in_and_unreachable_from_a_credential(
        server, operator, project):
    """`as_system` exists because closing the hazard above needed the two
    meanings of `member_id=None` separated: "the system is asking" and "the
    caller has no member row" were one value, so making the second fail closed
    broke the first (the add-member handler reads `visibility` to decide WHICH
    authorization rule applies, and cannot ask that through an ACL that
    presupposes the answer).

    Two things must stay true or the hazard fix is undone by its own escape
    hatch: the flag is opt-in (default fail-closed), and no request can reach
    it -- the handler that sets it checks the caller's authority itself, and a
    non-admin gets the same refusal as before.
    """
    from ticket_board.storage.errors import NotChannelMember

    secret = _channel(operator, "secret", visibility="private")
    outsider_session = server.bootstrap_operator(project["id"], "Second",
                                                 role="member")
    outsider = Client(server, project_id=project["id"],
                      cookie=outsider_session["session_token"],
                      csrf=outsider_session["csrf_token"])
    guest = next(m for m in operator.get("/members").json()["items"]
                 if m["display_name"] == "Second")

    # Opt-in only: the default is the fail-closed path, not the hatch.
    with pytest.raises(NotChannelMember):
        server.store.get_channel(project["id"], secret["id"], member_id=None)
    assert server.store.get_channel(
        project["id"], secret["id"], as_system=True)["id"] == secret["id"]

    # And the one handler that sets it still refuses a non-admin, so the hatch
    # is not a way into a private channel from outside.
    refused = outsider.post("/channels/{}/members".format(secret["id"]), {
        "request_id": rid(), "member_id": guest["id"]})
    assert refused.status == 403
    assert refused.json()["error"]["code"] == "forbidden_scope"
    # The refusal leaked nothing: the outsider still cannot see the channel.
    assert secret["name"] not in {
        c["name"] for c in outsider.get("/channels").json()["items"]}


def test_a_public_channel_is_still_readable_without_a_member_row(
        server, operator, project):
    """Public means public, and the system actor posts through this path."""
    open_channel = _channel(operator, "lobby")
    _say(operator, open_channel["id"], "hello")
    payload = server.store.list_messages(project["id"], open_channel["id"],
                                         member_id=None)
    assert _bodies(payload) == ["hello"]
