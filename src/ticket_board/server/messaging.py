"""Members, channels, messages and the outbox: the T-187 half of the API.

These are handlers, not a second router. They are bound into `app._ROUTE_TABLE`
and go through the same `_authorize`, the same error shapes and the same
`request_id` idempotency as every other route, because authorization for
messaging is not a different trust boundary -- it is the same one with one more
rule (channel membership) layered on the project scope.

Three things are decided here rather than in `storage.messaging`, and all three
are about the *credential* rather than the record:

* **Who the author is.** The record layer takes an actor dict and trusts it.
  Binding that dict to the presented credential -- and refusing a body that
  tries to supply one -- is an API-layer job, and it is the whole of
  `sender_identity_rejected`.
* **Which member a principal is.** An agent token names an agent; an operator
  cookie names a `mem_` id directly, because `auth.operator_id()` and
  `MemberId` share a shape on purpose (see `server/schema.py`). Everything
  downstream takes a member id, so the mapping happens once, here.
* **Whether an invite code is real.** The code is a bearer secret and lives in
  the server's credential tables, not on T-202's `invitations` record.
"""

from __future__ import annotations

import re

from ..storage.db import write_txn
from ..storage.errors import (
    MembershipRevoked,
    NotChannelMember,
    SenderIdentityRejected,
)
from . import validate
from .auth import in_seconds, mint_secret
from .errors import (
    ForbiddenScope,
    InvitationReplayRefused,
    MalformedRequest,
    NotFound,
)
from .wire import Response

DEFAULT_INVITATION_TTL_SECONDS = 86400
MESSAGE_PAGE_LIMIT = 50
MAX_MESSAGE_PAGE_LIMIT = 200

# Roles that may issue invitations and administer private channels. The
# contract says "owner/admin only" in two places and means the same pair.
ADMIN_ROLES = ("owner", "admin")

_CHANNEL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,59}$")


def _reject_author_field(body):
    """`sender_identity_rejected` before the generic body check can fire.

    `validate.check_body` already refuses identity fields, but as a 400
    `malformed_request` -- correct everywhere else, wrong here: the contract
    names 403 `sender_identity_rejected` for exactly this, and the distinction
    matters because the two say different things about *why* the request is
    refused. A shape error means "you sent a field I do not have"; this means
    "the author is not yours to assert."
    """
    if not isinstance(body, dict):
        return
    for field in ("author", "sender", "actor", "from"):
        if field in body:
            raise SenderIdentityRejected()


class MessagingRoutes:
    """Mixed into `BoardServer`. `self` is the server."""

    # ----------------------------------------------------------- identity

    def _member_id_of(self, principal, project_id):
        """The `mem_` id this credential acts as, or None for an unmapped one.

        Returns None rather than raising when a principal has no member row:
        the caller decides whether that is a refusal (posting a message) or
        simply a narrower read (listing public channels).
        """
        if principal is None:
            return None
        if principal.is_agent:
            row = self.store.conn.execute(
                "SELECT id, state FROM members WHERE project_id = ? AND agent_id = ?",
                (project_id, principal.agent_id),
            ).fetchone()
            if row is None:
                return None
            if row["state"] == "revoked":
                raise MembershipRevoked(row["id"])
            return row["id"]
        row = self.store.conn.execute(
            "SELECT id, state FROM members WHERE project_id = ? AND id = ?",
            (project_id, principal.operator_id),
        ).fetchone()
        if row is None:
            return None
        if row["state"] == "revoked":
            raise MembershipRevoked(row["id"])
        return row["id"]

    def _require_member_id(self, principal, project_id):
        """The member id, or 403 -- for routes that write into a conversation.

        A credential that is valid for the project but has no member record
        cannot be an author: every message carries a member-shaped identity and
        inventing one here would put an unaccountable actor in the trail.
        """
        member_id = self._member_id_of(principal, project_id)
        if member_id is None:
            raise ForbiddenScope(
                "This credential has no member record in that project."
            )
        return member_id

    def _member_role(self, project_id, member_id):
        row = self.store.conn.execute(
            "SELECT role FROM members WHERE project_id = ? AND id = ?",
            (project_id, member_id),
        ).fetchone()
        return row["role"] if row is not None else None

    def _require_admin(self, ctx, member_id, what):
        role = self._member_role(ctx.project_id, member_id)
        if role not in ADMIN_ROLES:
            raise ForbiddenScope("Only an owner or admin may {}.".format(what))

    def _adopt_member(self, project_id, operator, *, kind="human"):
        """Give an operator row the `Member` record the messaging API needs.

        `server/schema.py` mints operator ids with the `mem_` prefix precisely
        so this adoption is an insert and not a second identity. Idempotent, so
        a board that already has the row is unaffected.
        """
        existing = self.store.conn.execute(
            "SELECT id FROM members WHERE id = ?", (operator["id"],)
        ).fetchone()
        if existing is not None:
            return self.store.get_member(project_id, operator["id"])
        return self.store.create_member(
            project_id, kind, operator["display_name"], operator["role"],
            member_id=operator["id"],
        )

    def _adopt_agent_member(self, project_id, agent, *, role="member"):
        """The `kind=agent` member row that makes an agent addressable.

        Created when the agent is enrolled rather than lazily on first message:
        the Members list and a mention picker both have to show an agent before
        it has ever said anything.
        """
        existing = self.store.conn.execute(
            "SELECT id FROM members WHERE project_id = ? AND agent_id = ?",
            (project_id, agent["id"]),
        ).fetchone()
        if existing is not None:
            return self.store.get_member(project_id, existing["id"])
        return self.store.create_member(
            project_id, "agent", agent["name"], role, agent_id=agent["id"],
        )

    # ------------------------------------------------------------ members

    def list_members(self, ctx):
        # The store already returns the `{items: [...]}` envelope.
        return Response(200, self.store.list_members(ctx.project_id))

    # -------------------------------------------------------- invitations

    def create_invitation(self, ctx):
        body = validate.check_body(
            ctx.body(), required=("request_id", "role"), allowed=("ttl_seconds",),
        )
        request_id = validate.request_id(body)
        role = validate.enum(body, "role", ("owner", "admin", "member", "viewer"))
        ttl = validate.integer(body, "ttl_seconds", minimum=60, maximum=604800,
                               default=DEFAULT_INVITATION_TTL_SECONDS)
        # The route is operatorSession-only in the contract, so `_authorize` has
        # already refused agent tokens; this is the *role* check on top of it.
        member_id = self._require_member_id(ctx.principal, ctx.project_id)
        self._require_admin(ctx, member_id, "issue an invitation")

        created = self.store.create_invitation(
            ctx.project_id, role, in_seconds(ttl),
            created_by=ctx.principal.actor, request_id=request_id,
        )
        # A replayed request_id lands here after the first call already
        # minted, registered and returned a real code -- `store.create_invitation`
        # never fabricates or remembers a placeholder for it (T-286), only
        # `credentials` keeps a hash. Registering a second code would put two
        # live codes on one invitation, so the only honest answer left is to
        # refuse the replay (see `InvitationReplayRefused`).
        if self._invitation_code_exists(created["invitation"]["id"]):
            raise InvitationReplayRefused(request_id)
        code = mint_secret(24)
        self.credentials.remember_invitation_code(
            ctx.project_id, created["invitation"]["id"], code)
        return Response(201, {"invitation": created["invitation"], "code": code})

    def _invitation_code_exists(self, invitation_id):
        return self.store.conn.execute(
            "SELECT 1 FROM invitation_codes WHERE invitation_id = ?",
            (invitation_id,),
        ).fetchone() is not None

    def exchange_invitation(self, ctx):
        """Redeem an invite for a member record and a dashboard session.

        The contract is explicit that the session arrives as `Set-Cookie` and
        that no token appears in the body or a URL, so the 201 body is the bare
        `Member`. That is also why this route cannot mint an agent token: a
        human joining a project gets a browser session, not a bearer secret.
        """
        body = validate.check_body(
            ctx.body(), required=("request_id", "code", "display_name"),
        )
        validate.request_id(body)
        display_name = validate.text(body, "display_name", max_length=120)
        redeemed = self.credentials.consume_invitation_code(
            ctx.project_id, body.get("code"))

        operator = self.credentials.create_operator(
            ctx.project_id, display_name, role=redeemed["role"])
        member = self.store.create_member(
            ctx.project_id, "human", display_name, redeemed["role"],
            member_id=operator["id"],
        )
        self.credentials.spend_invitation_code(body.get("code"))
        self.store.conn.execute(
            "UPDATE invitations SET state = 'accepted', accepted_by = ?,"
            " version = version + 1 WHERE id = ?",
            (member["id"], redeemed["invitation_id"]),
        )
        session = self.credentials.open_operator_session(operator)
        self.store.conn.commit()

        response = Response(201, member)
        response.headers["Set-Cookie"] = (
            "tb_session={}; Path=/; HttpOnly; SameSite=Lax".format(
                session["session_token"])
        )
        # The CSRF partner cannot travel in the body (the schema is a bare
        # `Member`, additionalProperties: false) and must not be HttpOnly, or
        # the page could never read it back to send the header.
        response.headers["X-CSRF-Token"] = session["csrf_token"]
        return response

    # ----------------------------------------------------------- channels

    def list_channels(self, ctx):
        member_id = self._member_id_of(ctx.principal, ctx.project_id)
        # The store returns items + stream + empty_state already, and its
        # membership filter is the ACL: a private channel the caller does not
        # belong to is absent from the list, not flagged.
        return Response(200, self.store.list_channels(ctx.project_id,
                                                      member_id=member_id))

    def create_channel(self, ctx):
        body = validate.check_body(
            ctx.body(), required=("request_id", "name", "visibility"),
            allowed=("topic",),
        )
        request_id = validate.request_id(body)
        name = validate.text(body, "name", max_length=60)
        if not _CHANNEL_NAME_RE.match(name):
            raise MalformedRequest(
                "name must be lowercase letters, digits, - or _.",
                {"rejected_fields": ["name"]})
        visibility = validate.enum(body, "visibility", ("public", "private"))
        topic = validate.text(body, "topic", max_length=300, required=False)
        member_id = self._require_member_id(ctx.principal, ctx.project_id)

        channel = self.store.create_channel(
            ctx.project_id, name, visibility, topic=topic,
            actor=ctx.principal.actor, request_id=request_id,
        )
        # The creator joins their own private channel. Without this the author
        # of a private channel cannot read it, which is the kind of bug that
        # only shows up after the second request.
        if visibility == "private":
            self._ensure_channel_member(ctx.project_id, channel["id"], member_id)
            channel = self.store.get_channel(ctx.project_id, channel["id"],
                                             member_id=member_id)
        return Response(201, channel)

    def _ensure_channel_member(self, project_id, channel_id, member_id,
                               *, subscribed=True):
        row = self.store.conn.execute(
            "SELECT 1 FROM channel_members WHERE project_id = ? AND channel_id = ?"
            " AND member_id = ?", (project_id, channel_id, member_id),
        ).fetchone()
        if row is not None:
            return None
        return self.store.add_channel_member(
            project_id, channel_id, member_id, subscribed=subscribed)

    def add_channel_member(self, ctx):
        body = validate.check_body(
            ctx.body(), required=("request_id", "member_id"),
            allowed=("subscribed",),
        )
        request_id = validate.request_id(body)
        target = validate.text(body, "member_id", max_length=36)
        subscribed = validate.boolean(body, "subscribed", default=True)
        channel_id = ctx.params["channel_id"]
        caller = self._require_member_id(ctx.principal, ctx.project_id)

        channel = self.store.get_channel(ctx.project_id, channel_id)
        if channel["visibility"] == "private":
            # "Owner/admin only for private channels" -- and a member of the
            # channel cannot add others, because on a private channel the
            # membership list *is* the ACL.
            self._require_admin(ctx, caller, "add members to a private channel")
        self.store.get_member(ctx.project_id, target)
        added = self.store.add_channel_member(
            ctx.project_id, channel_id, target, subscribed=subscribed,
            request_id=request_id,
        )
        return Response(201, added)

    # ----------------------------------------------------------- messages

    def list_messages(self, ctx):
        channel_id = ctx.request.param("channel_id")
        if not channel_id:
            raise MalformedRequest("channel_id is required.",
                                   {"missing_fields": ["channel_id"]})
        thread_id = ctx.request.param("thread_id")
        limit = _page_limit(ctx.request.param("limit"))
        member_id = self._member_id_of(ctx.principal, ctx.project_id)
        payload = self.store.list_messages(
            ctx.project_id, channel_id, member_id=member_id,
            thread_id=thread_id, limit=limit,
        )
        return Response(200, payload)

    def send_message(self, ctx):
        raw = ctx.body()
        _reject_author_field(raw)
        body = validate.check_body(
            raw, required=("request_id", "channel_id", "body", "intent"),
            allowed=("thread_id", "mentions", "ticket_id", "causation_id"),
        )
        request_id = validate.request_id(body)
        channel_id = validate.text(body, "channel_id", max_length=36)
        text = validate.text(body, "body", max_length=16000)
        intent = validate.enum(body, "intent", ("message", "reply"))
        thread_id = validate.text(body, "thread_id", max_length=36, required=False)
        ticket_id = validate.text(body, "ticket_id", max_length=40, required=False)
        causation_id = validate.text(body, "causation_id", max_length=36,
                                     required=False)
        mentions = validate.string_list(body, "mentions", max_length=36) or []
        # The author is the credential. This is the single place it is bound,
        # and `_reject_author_field` above is the only reason a body could ever
        # have tried to say otherwise.
        self._require_member_id(ctx.principal, ctx.project_id)
        result = self.store.send_message(
            ctx.project_id, channel_id, ctx.principal.actor, text,
            intent=intent, thread_id=thread_id, mentions=mentions,
            ticket_id=ticket_id, causation_id=causation_id,
            request_id=request_id,
        )
        return Response(201, result)

    def list_deliveries(self, ctx):
        message_id = ctx.params["message_id"]
        member_id = self._member_id_of(ctx.principal, ctx.project_id)
        message = self._message_or_404(ctx.project_id, message_id)
        # Receipts are part of the conversation: seeing who a message reached
        # requires being able to read the channel it was posted in.
        self._assert_readable(ctx.project_id, message["channel_id"], member_id)
        payload = self.store.list_deliveries(ctx.project_id, message_id)
        return Response(200, payload)

    def _message_or_404(self, project_id, message_id):
        row = self.store.conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row is None or row["project_id"] != project_id:
            # Same answer for "another project's message" as for "no such
            # message": message ids must not be probeable across projects.
            raise NotFound("No such message.", {"message_id": message_id})
        return self.store._serialize_message(row)

    def _assert_readable(self, project_id, channel_id, member_id):
        self.store._assert_channel_visible(self.store.conn, project_id,
                                           channel_id, member_id)


def _page_limit(raw):
    if raw is None:
        return MESSAGE_PAGE_LIMIT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise MalformedRequest("limit must be an integer.",
                               {"rejected_fields": ["limit"]})
    if value < 1 or value > MAX_MESSAGE_PAGE_LIMIT:
        raise MalformedRequest(
            "limit must be between 1 and {}.".format(MAX_MESSAGE_PAGE_LIMIT),
            {"rejected_fields": ["limit"]})
    return value


class TaskRoutes:
    """`POST /messages/{id}/task`. Split out because it is the one route that
    has to reason about *dispatch*, not just about records.

    The contract fixes three outcomes and they are not error cases -- a queued
    task is a successful 201 that says why it is waiting. The distinction the
    fixtures draw is between "we could not do this" (4xx) and "we did exactly
    this, and here is the receipt explaining that nobody has been woken yet".
    """

    def send_task(self, ctx):
        raw = ctx.body()
        _reject_author_field(raw)
        body = validate.check_body(
            raw, required=("request_id", "outcome", "routing"), allowed=("ticket",),
        )
        request_id = validate.request_id(body)
        outcome = validate.text(body, "outcome", max_length=4000)
        routing = _routing(body)
        # This route owns its `request_id`, and it has to, because it is the
        # only one that writes through several store calls. Letting the inner
        # `send_message` spend the key instead -- what this handler did first --
        # made a byte-identical retry answer 409 `request_id_reused` and, worse,
        # leak a ticket on the way: the replay had already minted a second
        # draft ticket before `send_message` compared bodies, saw the new
        # ticket id in place of the old one, and refused. The contract's rule is
        # same id + same body = the stored response, so the check has to happen
        # before the first write, not in the middle. Same seam `master.py` uses
        # and for the same reason; `api-notes.md` flags it for promotion.
        message_id = ctx.params["message_id"]
        replay = self._task_replay(ctx.project_id, request_id, message_id, body)
        if replay is not None:
            return Response(201, replay)
        source = self._message_or_404(ctx.project_id, message_id)
        member_id = self._require_member_id(ctx.principal, ctx.project_id)
        # A task inherits the conversation's ACL: you cannot turn a message you
        # are not allowed to read into work for somebody else.
        channel = self.store._assert_channel_visible(
            self.store.conn, ctx.project_id, source["channel_id"], member_id)

        recipient = self._task_recipient(ctx, routing, channel)
        ticket = self._task_ticket(ctx, body.get("ticket"), outcome, request_id)

        message = self.store.send_message(
            ctx.project_id, source["channel_id"], ctx.principal.actor, outcome,
            intent="task", ticket_id=ticket["id"],
            causation_id=source["id"],
            recipient_agent_ids=[recipient["id"]] if recipient else [],
            # Not `request_id`: this handler already spent it above, and
            # `request_log`'s primary key is (project_id, request_id), so a
            # second store call under the same key would collide on insert.
            request_id=None,
            # Born `sent`: both onward states this route needs -- `delivered`
            # for a real dispatch and `queued` with a reason for the three
            # cases where nobody is woken -- are reachable only from `sent`.
            delivery_state="sent",
        )
        deliveries = message["deliveries"]
        wake_job = None
        if recipient is not None and deliveries:
            wake_job = self._dispatch(ctx, message["message"], deliveries[0],
                                      recipient, ticket)
            deliveries = self.store.list_deliveries(
                ctx.project_id, message["message"]["id"])["items"]

        payload = {
            "message": message["message"],
            "ticket": ticket,
            "deliveries": deliveries,
            "wake_job": wake_job,
        }
        self._remember_task(ctx.project_id, request_id, message_id, body, payload)
        return Response(201, payload)

    _TASK_OPERATION = "messaging.send_task"

    def _task_key(self, message_id, body):
        """What a retry has to match. The message id is part of it: the same
        `request_id` aimed at a *different* source message is a different
        request and must 409, not silently replay the first one's receipt."""
        return {"message_id": message_id, "outcome": body.get("outcome"),
                "routing": body.get("routing"), "ticket": body.get("ticket")}

    def _task_replay(self, project_id, request_id, message_id, body):
        with write_txn(self.store.conn) as conn:
            return self.store._replay(conn, project_id, request_id,
                                      self._TASK_OPERATION,
                                      self._task_key(message_id, body))

    def _remember_task(self, project_id, request_id, message_id, body, payload):
        with write_txn(self.store.conn) as conn:
            self.store._remember(conn, project_id, request_id,
                                 self._TASK_OPERATION,
                                 self._task_key(message_id, body), payload)

    def _task_recipient(self, ctx, routing, channel):
        """The agent this task is aimed at, or None when nobody is named.

        `via_master` is not a broadcast: the point of the mode is that one
        designated master decides the owner instead of every capable agent
        being woken. A channel without a designated master therefore has
        nowhere to send it, and that is a 422 rather than a silent fan-out.
        """
        if routing["mode"] == "direct":
            agent_id = routing.get("agent_id")
            if not agent_id:
                raise MalformedRequest(
                    "routing.agent_id is required when mode is direct.",
                    {"missing_fields": ["routing.agent_id"]})
            return self._agent_in_project(ctx.project_id, agent_id)
        designated = channel["designated_master"]
        if not designated:
            # The contract publishes 400 and 422 here but `ErrorCode` is frozen
            # and has no member for "this channel has no designated master".
            # `malformed_request` is the honest one of the two available: the
            # caller asked for a routing mode this channel cannot satisfy, and
            # the message says which field to change. Recorded for T-224.
            raise MalformedRequest(
                "This channel has no designated master; route the task directly.",
                {"rejected_fields": ["routing.mode"], "channel_id": channel["id"]})
        return self._agent_in_project(ctx.project_id, designated)

    def _agent_in_project(self, project_id, agent_id):
        agent = self.store.get_agent(agent_id)
        if agent["project_id"] != project_id:
            # Same shape as any other cross-project id: refused as out of
            # scope, never as "no such agent", so ids stay unprobeable.
            raise ForbiddenScope()
        return agent

    def _task_ticket(self, ctx, spec, outcome, request_id):
        """Link the named ticket, or create the draft the routing needs.

        The contract's `oneOf` is link-or-create and neither branch is
        optional in practice: routing a task at an agent without a ticket
        would produce work with no place to record progress.
        """
        if not isinstance(spec, dict) or len(spec) != 1:
            raise MalformedRequest(
                "ticket must name exactly one of existing_ticket_id or new_ticket.",
                {"rejected_fields": ["ticket"]})
        if "existing_ticket_id" in spec:
            return self.store.get_ticket(ctx.project_id,
                                         spec["existing_ticket_id"])
        draft = spec.get("new_ticket")
        if not isinstance(draft, dict) or "title" not in draft:
            raise MalformedRequest("new_ticket requires a title.",
                                   {"missing_fields": ["ticket.new_ticket.title"]})
        unexpected = sorted(f for f in draft if f not in ("title", "role"))
        if unexpected:
            raise MalformedRequest("Request body contained unexpected fields.",
                                   {"rejected_fields": unexpected})
        return self.store.create_ticket(
            ctx.project_id, self._next_ticket_id(ctx.project_id),
            draft["title"], role=draft.get("role"), outcome=outcome,
            acceptance=[], actor=ctx.principal.actor,
        )

    def _dispatch(self, ctx, message, delivery, recipient, ticket):
        """Wake a managed recipient, or record why nobody was woken.

        Every branch writes a receipt. A task that is not dispatched must still
        be visible as a delivery with a reason -- the failure this prevents is a
        task that looks sent because no error was raised and no row was written.
        """
        project = self.store.get_project(ctx.project_id)
        if project.get("paused"):
            self._queue(ctx, delivery, "project_paused",
                        "The project is paused; resume it to dispatch.")
            return None
        if recipient.get("connection_mode") != "managed":
            self._queue(ctx, delivery, "manual_resume_required",
                        "This agent connects by hook; resume it in its own session.")
            return None
        job = self.store.create_wake_job(
            ctx.project_id, message["id"], recipient["id"], delivery["id"],
            ticket_id=ticket["id"], actor=ctx.principal.actor,
        )
        self.store.transition_delivery(
            ctx.project_id, delivery["id"], "delivered",
            expected_version=delivery["version"], actor=ctx.principal.actor,
        )
        return job

    def _queue(self, ctx, delivery, reason, detail):
        self.store.transition_delivery(
            ctx.project_id, delivery["id"], "queued",
            expected_version=delivery["version"], reason=reason,
            reason_detail=detail, actor=ctx.principal.actor,
        )


def _routing(body):
    routing = body.get("routing")
    if not isinstance(routing, dict):
        raise MalformedRequest("routing must be an object.",
                               {"rejected_fields": ["routing"]})
    unexpected = sorted(f for f in routing if f not in ("mode", "agent_id"))
    if unexpected:
        raise MalformedRequest("Request body contained unexpected fields.",
                               {"rejected_fields": unexpected})
    if routing.get("mode") not in ("direct", "via_master"):
        raise MalformedRequest("routing.mode must be direct or via_master.",
                               {"rejected_fields": ["routing.mode"]})
    return routing
