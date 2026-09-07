"""Independent acceptance harness for T-187's three blocking defects (T-326).

FINDINGS AND RECIPE ARE opus-authz's, from the T-306 adversarial pass. This file
is opus-verify rebuilding them as committed, asserting tests, because T-306 left
its reproducers untracked in a detached worktree that T-316 is pruning toward --
the proof of three blocking defects survived only as prose and four scratch
files. Nothing here is a new finding.

WHY THIS FILE IS NOT WRITTEN BY THE AUTHOR OF THE FIX. T-325 fixes T-187 and is
held by opus-backend-2, who wrote T-187. An author writing the acceptance test
for their own fix is the weakest cover available, so these criteria were written
against the *requirement* before the fix existed and were not shaped by it.

Each test therefore asserts the contract obligation, never the shape of a
particular repair:

  F1  every message posted to a channel is reachable through the paging
      parameters the contract declares (`cursor`, `limit`), and `next_cursor`
      is honest about whether more remain.
  F2  a byte-identical replay of POST /invitations/exchange returns the
      original result -- including the session -- and creates no second member;
      the same request_id under a *different* body is 409 request_id_reused.
  F3  POST /messages refuses a ticket_id that names another project's ticket or
      no ticket at all, and whatever it does return validates against the
      frozen contract.

Two of these deliberately assert BOTH directions. A pager that returns nothing
and a validator that refuses everything would satisfy a one-directional test
while destroying the feature, so the legitimate cases are pinned next to the
refusals.

Run with the contract toolchain armed, or `check()` degrades to a skip and a
skipped contract assertion reads exactly like a passing one:

    PYTHONPATH=src TICKET_BOARD_CONTRACTS_REQUIRED=1 \
        /private/tmp/bare-venv/bin/pytest tests/server/test_t326_t187_acceptance.py
"""

import pytest

from api_client import Client, rid
from api_spec import check

# A cursor walk on a broken pager can loop. Bound it well above any page count
# these tests produce, so a non-terminating pager fails with a diagnosis rather
# than hanging the suite.
MAX_PAGES = 40


def _channel(operator, name, visibility="public", **extra):
    body = {"request_id": rid(), "name": name, "visibility": visibility}
    body.update(extra)
    response = operator.post("/channels", body)
    assert response.status == 201, response.json()
    return response.json()


def _say(client, channel_id, text, **extra):
    body = {"request_id": rid(), "channel_id": channel_id, "body": text,
            "intent": "message"}
    body.update(extra)
    return client.post("/messages", body)


def _fill(operator, channel_id, count):
    """Post `count` ordered messages and return the bodies in post order."""
    bodies = []
    for i in range(count):
        text = "m{:03d}".format(i)
        response = _say(operator, channel_id, text)
        assert response.status == 201, response.json()
        bodies.append(text)
    return bodies


def _walk(client, channel_id, query_extra=""):
    """Page the channel to exhaustion and return (bodies, pages_read).

    Follows `next_cursor` exactly as a contract-conformant client would: request
    a page, stop when `next_cursor` is null, otherwise pass it back as `cursor`.
    Fails loudly on the two ways a pager can refuse to terminate.
    """
    bodies = []
    cursor = None
    seen_cursors = set()
    for page_number in range(MAX_PAGES):
        query = "channel_id=" + channel_id + query_extra
        if cursor is not None:
            query += "&cursor=" + str(cursor)
        response = client.get("/messages", query=query)
        assert response.status == 200, response.json()
        payload = response.json()
        # Every page must be a contract-valid response, not just the first.
        check("MessageListResponse", payload,
              label="GET /messages page {}".format(page_number))
        bodies.extend(m["body"] for m in payload["items"])
        cursor = payload.get("next_cursor")
        if cursor is None:
            return bodies, page_number + 1
        assert cursor not in seen_cursors, (
            "pager repeated cursor {!r} at page {} -- next_cursor does not "
            "advance, so a conformant client loops forever".format(
                cursor, page_number))
        seen_cursors.add(cursor)
    pytest.fail(
        "pager did not terminate within {} pages; collected {} messages"
        .format(MAX_PAGES, len(bodies)))


# --------------------------------------------------------------------- F1
# GET /messages: the newest messages in a busy channel must be reachable, and
# next_cursor must not claim completeness while messages remain.


def test_f1_every_message_is_reachable_by_paging_a_busy_channel(
        server, operator, project):
    """60 messages into one channel; paging must yield all 60, in post order.

    Pre-fix this returns the OLDEST 50 with next_cursor=null: the 10 newest are
    absent and the payload asserts there is no next page.
    """
    channel = _channel(operator, "busy")
    posted = _fill(operator, channel["id"], 60)

    seen, pages = _walk(operator, channel["id"])

    assert seen == posted, (
        "paging did not return the conversation: posted {} messages, paging "
        "yielded {} in {} page(s); missing={} unexpected={}".format(
            len(posted), len(seen), pages,
            [b for b in posted if b not in seen],
            [b for b in seen if b not in posted]))


def test_f1_next_cursor_is_not_null_while_messages_remain(
        server, operator, project):
    """The honesty half of F1, isolated from the walk.

    A first page that is short of the channel and still says next_cursor=null
    tells the client the conversation is complete when it is not. This is the
    assertion that makes the defect a *lie* rather than a small default.
    """
    channel = _channel(operator, "honest")
    posted = _fill(operator, channel["id"], 60)

    response = operator.get("/messages", query="channel_id=" + channel["id"])
    assert response.status == 200, response.json()
    page = response.json()
    returned = [m["body"] for m in page["items"]]

    if len(returned) < len(posted):
        assert page["next_cursor"] is not None, (
            "first page returned {} of {} messages but next_cursor is null -- "
            "the response asserts the channel is complete while {!r}..{!r} are "
            "unreachable".format(len(returned), len(posted),
                                 posted[len(returned)], posted[-1]))


def test_f1_declared_cursor_parameter_is_not_silently_ignored(
        server, operator, project):
    """`cursor` is a declared parameter (components/parameters/Cursor).

    Pre-fix the handler never reads it, so page 2 is byte-identical to page 1
    and a client that pages loops on the first page forever. Either the cursor
    advances or it is rejected -- silently returning page 1 is the one answer
    that cannot be told apart from success.
    """
    channel = _channel(operator, "paged")
    _fill(operator, channel["id"], 60)

    first = operator.get("/messages", query="channel_id=" + channel["id"])
    assert first.status == 200, first.json()
    page_one = first.json()
    cursor = page_one["next_cursor"]
    assert cursor is not None, (
        "no next_cursor offered for a 60-message channel, so the declared "
        "cursor parameter can never be exercised at all")

    second = operator.get(
        "/messages",
        query="channel_id={}&cursor={}".format(channel["id"], cursor))
    assert second.status == 200, second.json()
    page_two = second.json()

    ids_one = [m["id"] for m in page_one["items"]]
    ids_two = [m["id"] for m in page_two["items"]]
    assert ids_two != ids_one, (
        "GET /messages?cursor={} returned the identical page -- the declared "
        "cursor parameter is ignored, so a paging client never advances"
        .format(cursor))
    assert not (set(ids_one) & set(ids_two)), (
        "page 2 re-delivers {} message(s) already on page 1; a forward cursor "
        "must not overlap".format(len(set(ids_one) & set(ids_two))))


def test_f1_messages_past_the_maximum_limit_are_reachable(
        server, operator, project):
    """210 messages at limit=200, the contract maximum.

    Pre-fix m200..m209 are unreachable through EVERY parameter combination the
    contract offers: the channel is frozen at its first 200 messages forever.
    """
    channel = _channel(operator, "flood")
    posted = _fill(operator, channel["id"], 210)

    seen, pages = _walk(operator, channel["id"], query_extra="&limit=200")

    assert seen == posted, (
        "at the contract's maximum limit, {} of {} messages are unreachable "
        "in {} page(s); newest reached was {!r}, newest posted was {!r}".format(
            len(posted) - len(seen), len(posted), pages,
            seen[-1] if seen else None, posted[-1]))


def test_f1_limit_is_honoured_and_bounded(server, operator, project):
    """The other direction: `limit` must still work and still be bounded.

    T-306 notes the author's suite has ZERO tests exercising `limit`. A pager
    fixed by ignoring `limit` and returning everything would pass the four
    reachability tests above; this one refuses that repair.
    """
    channel = _channel(operator, "bounded")
    _fill(operator, channel["id"], 60)

    small = operator.get(
        "/messages", query="channel_id={}&limit=10".format(channel["id"]))
    assert small.status == 200, small.json()
    assert len(small.json()["items"]) == 10, (
        "limit=10 returned {} items".format(len(small.json()["items"])))

    over = operator.get(
        "/messages", query="channel_id={}&limit=201".format(channel["id"]))
    assert over.status == 400, (
        "limit=201 exceeds the contract maximum of 200 but returned {}"
        .format(over.status))

    bad = operator.get(
        "/messages", query="channel_id={}&limit=abc".format(channel["id"]))
    assert bad.status == 400, (
        "non-integer limit returned {}".format(bad.status))


# --------------------------------------------------------------------- F2
# POST /invitations/exchange takes a request_id and never uses it. A retry --
# the ordinary shape of a lost 201 -- strands a real person outside a project
# they are already a member of, and the contract has no sign-in route.


def _invite(operator, role="member"):
    response = operator.post("/invitations", {"request_id": rid(), "role": role})
    assert response.status == 201, response.json()
    return response.json()["code"]


def _members(operator):
    response = operator.get("/members")
    assert response.status == 200, response.json()
    return response.json()["items"]


def test_f2_identical_replay_returns_the_original_member_and_a_session(
        server, operator, project):
    """The headline: a byte-identical retry must return the original result.

    RequestId in the frozen contract: "Replaying a mutation with the same
    request_id and an identical body returns the original result." Pre-fix the
    replay is 422 enrollment_code_invalid with no Set-Cookie, while the member
    row already exists -- the invitee is a member holding no session, the code
    is spent, and there is no recovery but an admin minting a second invitation.
    """
    code = _invite(operator)
    body = {"request_id": rid(), "code": code, "display_name": "Dana"}

    first = Client(server, project_id=project["id"]).post(
        "/invitations/exchange", dict(body))
    assert first.status == 201, first.json()
    assert "tb_session" in first.headers.get("Set-Cookie", ""), (
        "first exchange did not set a session cookie")

    replay = Client(server, project_id=project["id"]).post(
        "/invitations/exchange", dict(body))

    assert replay.status == first.status, (
        "replay of an identical request_id returned {} where the original "
        "returned {}: {}".format(replay.status, first.status, replay.json()))
    assert replay.json() == first.json(), (
        "replay returned a different body than the original result:\n"
        "  first:  {}\n  replay: {}".format(first.json(), replay.json()))
    assert "tb_session" in replay.headers.get("Set-Cookie", ""), (
        "replay did not re-set the session cookie, so the invitee is a member "
        "of the project holding no session -- and the frozen contract has no "
        "operator sign-in route, so the seat is unrecoverable")


def test_f2_replay_creates_no_second_member(server, operator, project):
    """Idempotency's other half: the retry must not double-enrol."""
    code = _invite(operator)
    body = {"request_id": rid(), "code": code, "display_name": "Dana"}

    Client(server, project_id=project["id"]).post(
        "/invitations/exchange", dict(body))
    Client(server, project_id=project["id"]).post(
        "/invitations/exchange", dict(body))

    danas = [m for m in _members(operator) if m["display_name"] == "Dana"]
    assert len(danas) == 1, (
        "replay produced {} member rows for one invitation: {}".format(
            len(danas), [m["id"] for m in danas]))


def test_f2_same_request_id_with_a_different_body_is_409(
        server, operator, project):
    """The refusal direction, so a fix cannot pass by replaying everything.

    Contract: "replaying it with a different body is 409 request_id_reused."
    Pre-fix the request_id is never recorded, so this second, *different*
    exchange simply succeeds and quietly enrols a second person under the
    caller's key.
    """
    reused = rid()
    first = Client(server, project_id=project["id"]).post(
        "/invitations/exchange",
        {"request_id": reused, "code": _invite(operator),
         "display_name": "Dana"})
    assert first.status == 201, first.json()

    different = Client(server, project_id=project["id"]).post(
        "/invitations/exchange",
        {"request_id": reused, "code": _invite(operator),
         "display_name": "Erin"})

    assert different.status == 409, (
        "same request_id with a different body returned {} ({}), not 409 "
        "request_id_reused".format(different.status, different.json()))
    assert different.json().get("error", {}).get("code") == "request_id_reused", (
        "409 carried the wrong error code: {}".format(different.json()))


def test_f2_a_fresh_invitation_still_enrols(server, operator, project):
    """Both directions: replay protection must not break ordinary enrolment."""
    joined = Client(server, project_id=project["id"]).post(
        "/invitations/exchange",
        {"request_id": rid(), "code": _invite(operator),
         "display_name": "Fresh"})
    assert joined.status == 201, joined.json()
    assert "Fresh" in [m["display_name"] for m in _members(operator)]


# --------------------------------------------------------------------- F3
# POST /messages applies validate.text(ticket_id, max_length=40) and nothing
# else -- no pattern, no existence check, no project scope -- while the same
# handler resolves causation_id project-scoped and the sibling task route calls
# store.get_ticket(project_id, ...). The check is absent only here.


def _other_project_with_ticket(server):
    other = server.store.create_project("Other")
    ticket = server.store.create_ticket(
        other["id"], "OTHER-1", "Their secret roadmap",
        acceptance=["x"], actor={"type": "system", "id": "sys"})
    return other, ticket


def test_f3_a_ticket_from_another_project_is_refused(server, operator, project):
    """Pre-fix this is a 201: a permanent cross-project reference written into
    the message record and the audit trail."""
    _other, foreign = _other_project_with_ticket(server)
    channel = _channel(operator, "general")

    response = _say(operator, channel["id"], "linked", ticket_id=foreign["id"])

    assert response.status >= 400, (
        "POST /messages linked ticket {!r} from another project and returned "
        "{}; the message record now carries a cross-project reference: {}"
        .format(foreign["id"], response.status,
                response.json().get("message", {}).get("ticket_id")))


def test_f3_a_ticket_that_does_not_exist_is_refused(server, operator, project):
    channel = _channel(operator, "general")

    response = _say(operator, channel["id"], "bogus",
                    ticket_id="T-DOES-NOT-EXIST")

    assert response.status >= 400, (
        "POST /messages accepted ticket_id='T-DOES-NOT-EXIST' with {}"
        .format(response.status))


def test_f3_the_response_validates_against_the_frozen_contract(
        server, operator, project):
    """Separate from the refusal, per T-326: whatever comes back must conform.

    This is the assertion that holds even if the project later decides free
    ticket references are acceptable -- what is not acceptable is a 201 whose
    own body violates the frozen schema. TicketId is
    `^[A-Z][A-Z0-9]{1,15}-[0-9]{1,6}$` (openapi.yaml:182), and pre-fix the
    route ships a 201 that fails the author's own checker.
    """
    channel = _channel(operator, "general")

    response = _say(operator, channel["id"], "bogus",
                    ticket_id="T-DOES-NOT-EXIST")

    if response.status == 201:
        check("SendMessageResponse", response.json(),
              label="POST /messages 201 with an unvalidated ticket_id")
    else:
        check("Error", response.json(),
              label="POST /messages refusal of an unvalidated ticket_id")


def test_f3_a_real_ticket_in_this_project_is_still_accepted(
        server, operator, project, ticket):
    """Both directions. A fix that refuses every ticket_id is not a pass: the
    field exists so a message can cite the work it is about."""
    channel = _channel(operator, "general")

    response = _say(operator, channel["id"], "linked", ticket_id=ticket["id"])

    assert response.status == 201, (
        "a message citing this project's own ticket {!r} was refused: {}"
        .format(ticket["id"], response.json()))
    assert response.json()["message"]["ticket_id"] == ticket["id"]
    check("SendMessageResponse", response.json(),
          label="POST /messages 201 with a valid ticket_id")


def test_f3_a_message_without_a_ticket_is_still_accepted(
        server, operator, project):
    """ticket_id is optional; validation must not make it required."""
    channel = _channel(operator, "general")
    response = _say(operator, channel["id"], "no ticket here")
    assert response.status == 201, response.json()


# --------------------------------------------------- F4, defence in depth
# The hazard T-306 recorded but explicitly did NOT count as a finding, because
# it could not be reached through the API: every principal-creating site adopts
# a member row today. T-325 picked it up anyway, so it gets cover here -- at the
# storage layer, which is where the asymmetry lives and where it IS reachable.
#
# It matters because `_assert_channel_visible` fails OPEN for member_id=None
# while the SSE filter for the identical question fails CLOSED, in the same
# commit. A legacy/imported board (T-213) or any future principal that skips
# adoption lands on the open side.


def test_f4_storage_does_not_show_private_channels_to_an_unmapped_principal(
        server, operator, project):
    channel = _channel(operator, "secret", "private")
    assert _say(operator, channel["id"], "classified").status == 201

    with pytest.raises(Exception) as excinfo:
        server.store.list_messages(project["id"], channel["id"], member_id=None)

    assert "NotChannelMember" in type(excinfo.value).__name__ or \
        "Forbidden" in type(excinfo.value).__name__, (
            "a principal with no member row read a private channel's messages; "
            "_assert_channel_visible treats member_id=None as see-everything, "
            "so it fails OPEN where the SSE filter for the same question fails "
            "closed (raised {})".format(type(excinfo.value).__name__))


def test_f4_storage_still_shows_public_channels_to_an_unmapped_principal(
        server, operator, project):
    """Both directions: narrowing the None case must not close public reads."""
    channel = _channel(operator, "lobby", "public")
    assert _say(operator, channel["id"], "hello").status == 201

    payload = server.store.list_messages(project["id"], channel["id"],
                                         member_id=None)
    assert [m["body"] for m in payload["items"]] == ["hello"]
