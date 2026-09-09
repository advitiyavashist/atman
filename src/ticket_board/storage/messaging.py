"""Messaging, delivery and runner records for the Ticket Board store."""

import base64
import json

from . import ids
from .db import read_txn, write_txn
from .errors import (
    ForbiddenScope,
    InvalidStateTransition,
    MalformedRequest,
    MembershipRevoked,
    NotChannelMember,
    NotFound,
    RunAlreadyActive,
    SenderIdentityRejected,
    TicketVersionConflict,
)

MEMBER_KINDS = {"human", "agent", "master"}
PROJECT_ROLES = {"owner", "admin", "member", "viewer"}
MEMBER_STATES = {"active", "revoked"}
AVAILABILITY = {"available", "busy", "offline", "unknown"}
CHANNEL_KINDS = {"channel", "dm"}
VISIBILITY = {"public", "private"}
MESSAGE_INTENTS = {"message", "task", "reply", "receipt"}
DELIVERY_STATES = {
    "sent", "queued", "delivered", "started", "responded",
    "blocked", "awaiting_approval", "canceled", "failed",
}
DELIVERY_REASONS = {
    "runner_offline", "manual_resume_required", "agent_busy",
    "dependency_unmet", "permission_required", "budget_exceeded",
    "project_paused", "canceled_by_operator", "dispatch_failed",
}
WAKE_STATES = {"pending", "leased", "completed", "failed", "canceled"}
RUN_STATES = {"pending", "starting", "running", "paused", "responded", "canceled", "failed"}

DELIVERY_TRANSITIONS = {
    "sent": {"queued", "delivered", "blocked", "awaiting_approval", "canceled", "failed"},
    "queued": {"delivered", "started", "blocked", "awaiting_approval", "canceled", "failed"},
    "delivered": {"started", "responded", "canceled", "failed"},
    "started": {"responded", "awaiting_approval", "canceled", "failed"},
    "blocked": {"queued", "canceled", "failed"},
    "awaiting_approval": {"queued", "started", "canceled", "failed"},
    "responded": set(),
    "canceled": set(),
    "failed": set(),
}

RUN_EVENT_TARGETS = {
    "starting": "starting",
    "started": "running",
    "turn_completed": "running",
    "needs_approval": "paused",
    "responded": "responded",
    "failed": "failed",
    "budget_reached": "paused",
}

DEFAULT_BUDGET = {
    "max_hops": 3,
    "max_turns": 10,
    "max_seconds": 900,
    "hops_used": 0,
    "turns_used": 0,
    "seconds_used": 0,
}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _omit_none(payload, fields):
    for field in fields:
        if payload.get(field) is None:
            payload.pop(field, None)
    return payload


def _assert_member(value, allowed, field):
    if value not in allowed:
        raise MalformedRequest("{} is not a valid value.".format(field), {field: value})


def _budget(value=None):
    result = dict(DEFAULT_BUDGET)
    if value:
        result.update(value)
    return result


class MessagingMixin:
    """BoardStore methods for T-202 messaging persistence."""

    # --------------------------------------------------------------- members

    def _member_row(self, conn, project_id, member_id):
        row = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
        if row is None:
            raise NotFound("No such member.", {"member_id": member_id})
        if row["project_id"] != project_id:
            raise ForbiddenScope(project_id, member_id)
        return row

    def _serialize_member(self, row):
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "kind": row["kind"],
            "display_name": row["display_name"],
            "role": row["role"],
            "state": row["state"],
            "agent_id": row["agent_id"],
            "availability": row["availability"],
            "version": row["version"],
            "created_at": row["created_at"],
        }

    def create_member(self, project_id, kind, display_name, role, *, agent_id=None,
                      availability="unknown", member_id=None, request_id=None,
                      actor=None):
        _assert_member(kind, MEMBER_KINDS, "kind")
        _assert_member(role, PROJECT_ROLES, "role")
        _assert_member(availability, AVAILABILITY, "availability")
        mid = member_id or ids.member_id()
        body = {
            "kind": kind, "display_name": display_name, "role": role,
            "agent_id": agent_id, "availability": availability,
        }
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id, "create_member", body)
            if replay is not None:
                return replay
            if agent_id is not None:
                agent = conn.execute(
                    "SELECT project_id FROM agents WHERE id = ?", (agent_id,)
                ).fetchone()
                if agent is None:
                    raise NotFound("No such agent.", {"agent_id": agent_id})
                if agent["project_id"] != project_id:
                    raise ForbiddenScope(project_id, agent_id)
            now = ids.now()
            conn.execute(
                "INSERT INTO members (id, project_id, kind, display_name, role, state,"
                " agent_id, availability, version, created_at)"
                " VALUES (?, ?, ?, ?, ?, 'active', ?, ?, 1, ?)",
                (mid, project_id, kind, display_name, role, agent_id, availability, now),
            )
            self._audit(conn, project_id, actor or self.SYSTEM_ACTOR,
                        "member.create", subject_type="member", subject_id=mid,
                        request_id=request_id,
                        summary="created member {}".format(display_name))
            result = self._serialize_member(self._member_row(conn, project_id, mid))
            self._remember(conn, project_id, request_id, "create_member", body, result)
            return result

    def get_member(self, project_id, member_id):
        with read_txn(self.conn) as conn:
            return self._serialize_member(self._member_row(conn, project_id, member_id))

    def list_members(self, project_id):
        rows = self.conn.execute(
            "SELECT * FROM members WHERE project_id = ? ORDER BY display_name, id",
            (project_id,),
        ).fetchall()
        return {"items": [self._serialize_member(r) for r in rows]}

    def revoke_member(self, project_id, member_id, *, expected_version,
                      request_id=None, actor=None):
        body = {"member_id": member_id, "expected_version": expected_version}
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id, "revoke_member", body)
            if replay is not None:
                return replay
            row = self._member_row(conn, project_id, member_id)
            if row["version"] != expected_version:
                raise TicketVersionConflict(member_id, expected_version, row["version"])
            conn.execute(
                "UPDATE members SET state = 'revoked', version = version + 1 WHERE id = ?",
                (member_id,),
            )
            conn.execute(
                "UPDATE deliveries SET state = 'blocked', reason = 'permission_required',"
                " reason_detail = 'membership revoked', version = version + 1,"
                " updated_at = ? WHERE project_id = ? AND recipient_agent_id IN ("
                " SELECT agent_id FROM members WHERE id = ? AND agent_id IS NOT NULL)"
                " AND state IN ('sent', 'queued')",
                (ids.now(), project_id, member_id),
            )
            self._audit(conn, project_id, actor or self.SYSTEM_ACTOR,
                        "member.revoke", subject_type="member", subject_id=member_id,
                        request_id=request_id, summary="revoked membership")
            result = self._serialize_member(self._member_row(conn, project_id, member_id))
            self._remember(conn, project_id, request_id, "revoke_member", body, result)
            return result

    # ------------------------------------------------------------ invitations

    def _serialize_invitation(self, row, *, include_created_by=True):
        payload = {
            "id": row["id"],
            "project_id": row["project_id"],
            "role": row["role"],
            "state": row["state"],
            "created_by": json.loads(row["created_by"]) if row["created_by"] else None,
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "accepted_by": row["accepted_by"],
        }
        if not include_created_by:
            payload.pop("created_by", None)
        return _omit_none(payload, ("created_by",))

    def create_invitation(self, project_id, role, expires_at, *, created_by=None,
                          invitation_id=None, code=None, request_id=None):
        """Create an invitation. `code` is for direct/offline callers only.

        The API route (`server/messaging.py`) never passes `code` -- it mints
        and hashes its own, registering it in `credentials` rather than here.
        This method used to fabricate a throwaway code when none was given
        and remember THAT in the replay log (T-286): a byte-identical replay
        then served a well-formed placeholder that was never registered and
        422'd on redemption. Minting nothing when `code` is omitted, and
        omitting the key entirely from what gets remembered, means the replay
        log can no longer hold a string that looks like a redeemable secret.
        """
        _assert_member(role, PROJECT_ROLES, "role")
        iid = invitation_id or ids.invitation_id()
        body = {"role": role, "expires_at": expires_at}
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id, "create_invitation", body)
            if replay is not None:
                return replay
            now = ids.now()
            conn.execute(
                "INSERT INTO invitations (id, project_id, role, state, created_by,"
                " created_at, expires_at, accepted_by, version)"
                " VALUES (?, ?, ?, 'pending', ?, ?, ?, NULL, 1)",
                (iid, project_id, role, _json(created_by) if created_by else None,
                 now, expires_at),
            )
            self._audit(conn, project_id, created_by or self.SYSTEM_ACTOR,
                        "invitation.create", subject_type="member", subject_id=iid,
                        request_id=request_id, summary="created invitation")
            invitation = self._serialize_invitation(
                conn.execute("SELECT * FROM invitations WHERE id = ?", (iid,)).fetchone()
            )
            result = {"invitation": invitation}
            if code is not None:
                result["code"] = code
            self._remember(conn, project_id, request_id, "create_invitation", body, result)
            return result

    # --------------------------------------------------------------- channels

    def _channel_row(self, conn, project_id, channel_id):
        row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        if row is None:
            raise NotFound("No such channel.", {"channel_id": channel_id})
        if row["project_id"] != project_id:
            raise ForbiddenScope(project_id, channel_id)
        return row

    def _channel_unread_count(self, conn, project_id, channel_id):
        row = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE project_id = ? AND channel_id = ?",
            (project_id, channel_id),
        ).fetchone()
        return int(row[0])

    def _serialize_channel(self, conn, row):
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "name": row["name"],
            "kind": row["kind"],
            "visibility": row["visibility"],
            "topic": row["topic"],
            "designated_master": row["designated_master"],
            "unread_count": self._channel_unread_count(
                conn, row["project_id"], row["id"]
            ),
            "version": row["version"],
            "created_at": row["created_at"],
        }

    def _serialize_channel_member(self, row):
        return {
            "channel_id": row["channel_id"],
            "member_id": row["member_id"],
            "subscribed": bool(row["subscribed"]),
            "joined_at": row["joined_at"],
        }

    def _assert_channel_visible(self, conn, project_id, channel_id, member_id,
                                *, as_system=False):
        channel = self._channel_row(conn, project_id, channel_id)
        if as_system:
            # NOT a principal, and never reachable from a credential: an
            # internal lookup that needs the channel row in order to decide
            # authorization itself. The one caller is the add-member handler,
            # which reads `visibility` to choose between "any member may add"
            # and "owner/admin only" -- it cannot ask that question through an
            # ACL that presupposes the answer. Keeping this as its own argument
            # is the point of the change below: "the system is asking" and "the
            # caller has no member row" were the same value before, so making
            # one of them fail closed would have broken the other.
            return channel
        if member_id is None:
            # T-325, defence in depth. This used to return the channel here
            # unconditionally -- a principal with NO member row was treated as
            # "everyone", which is the opposite of the SSE filter's answer to
            # the identical question in the same commit (events.py fails closed
            # via _UNRESOLVED_CHANNEL). opus-authz could not reach it through
            # the API today because all three principal-creating sites adopt a
            # member row, so this closes a door rather than fixing a live leak:
            # an imported board (T-213) or any future principal that skips
            # adoption would otherwise land on the open side of it.
            #
            # A public channel is still readable without a member row -- that
            # is what public means, and the system actor posts through this
            # path -- but a private channel's membership list IS its ACL, and
            # "no member row" cannot satisfy it.
            if channel["visibility"] != "public":
                raise NotChannelMember(channel_id, None)
            return channel
        if channel["visibility"] == "public":
            member = self._member_row(conn, project_id, member_id)
            if member["state"] == "revoked":
                raise MembershipRevoked(member_id)
            return channel
        member = self._member_row(conn, project_id, member_id)
        if member["state"] == "revoked":
            raise MembershipRevoked(member_id)
        row = conn.execute(
            "SELECT 1 FROM channel_members WHERE project_id = ?"
            " AND channel_id = ? AND member_id = ?",
            (project_id, channel_id, member_id),
        ).fetchone()
        if row is None:
            raise NotChannelMember(channel_id, member_id)
        return channel

    def create_channel(self, project_id, name, visibility, *, kind="channel",
                       topic=None, designated_master=None, channel_id=None,
                       request_id=None, actor=None):
        _assert_member(kind, CHANNEL_KINDS, "kind")
        _assert_member(visibility, VISIBILITY, "visibility")
        cid = channel_id or ids.channel_id()
        body = {
            "name": name, "visibility": visibility, "kind": kind,
            "topic": topic, "designated_master": designated_master,
        }
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id, "create_channel", body)
            if replay is not None:
                return replay
            if designated_master is not None:
                agent = conn.execute(
                    "SELECT project_id FROM agents WHERE id = ?", (designated_master,)
                ).fetchone()
                if agent is None:
                    raise NotFound("No such agent.", {"agent_id": designated_master})
                if agent["project_id"] != project_id:
                    raise ForbiddenScope(project_id, designated_master)
            now = ids.now()
            conn.execute(
                "INSERT INTO channels (id, project_id, name, kind, visibility, topic,"
                " designated_master, version, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (cid, project_id, name, kind, visibility, topic, designated_master, now),
            )
            self._audit(conn, project_id, actor or self.SYSTEM_ACTOR,
                        "channel.create", subject_type="channel", subject_id=cid,
                        request_id=request_id,
                        summary="created channel {}".format(name))
            result = self._serialize_channel(conn, self._channel_row(conn, project_id, cid))
            self._remember(conn, project_id, request_id, "create_channel", body, result)
            return result

    def get_channel(self, project_id, channel_id, *, member_id=None, as_system=False):
        with read_txn(self.conn) as conn:
            return self._serialize_channel(
                conn,
                self._assert_channel_visible(conn, project_id, channel_id, member_id,
                                             as_system=as_system),
            )

    def list_channels(self, project_id, *, member_id=None):
        with read_txn(self.conn) as conn:
            if member_id is not None:
                member = self._member_row(conn, project_id, member_id)
                if member["state"] == "revoked":
                    raise MembershipRevoked(member_id)
                rows = conn.execute(
                    "SELECT DISTINCT c.* FROM channels c"
                    " LEFT JOIN channel_members cm ON cm.channel_id = c.id"
                    " WHERE c.project_id = ? AND (c.visibility = 'public'"
                    " OR cm.member_id = ?) ORDER BY c.name, c.id",
                    (project_id, member_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM channels WHERE project_id = ? ORDER BY name, id",
                    (project_id,),
                ).fetchall()
            payload = {
                "items": [self._serialize_channel(conn, r) for r in rows],
                "stream": self._stream_status(conn, project_id),
            }
            if not rows:
                from ..screen_copy import EMPTY_STATES
                payload["empty_state"] = dict(EMPTY_STATES["channels"])
            return payload

    def add_channel_member(self, project_id, channel_id, member_id, *, subscribed=True,
                           request_id=None, actor=None):
        body = {"channel_id": channel_id, "member_id": member_id,
                "subscribed": bool(subscribed)}
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id,
                                  "add_channel_member", body)
            if replay is not None:
                return replay
            self._channel_row(conn, project_id, channel_id)
            member = self._member_row(conn, project_id, member_id)
            if member["state"] == "revoked":
                raise MembershipRevoked(member_id)
            now = ids.now()
            conn.execute(
                "INSERT INTO channel_members (project_id, channel_id, member_id,"
                " subscribed, joined_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(channel_id, member_id) DO UPDATE SET"
                " subscribed = excluded.subscribed",
                (project_id, channel_id, member_id, 1 if subscribed else 0, now),
            )
            self._audit(conn, project_id, actor or self.SYSTEM_ACTOR,
                        "channel.member.add", subject_type="channel",
                        subject_id=channel_id, request_id=request_id,
                        summary="added member {}".format(member_id))
            row = conn.execute(
                "SELECT * FROM channel_members WHERE channel_id = ? AND member_id = ?",
                (channel_id, member_id),
            ).fetchone()
            result = self._serialize_channel_member(row)
            self._remember(conn, project_id, request_id,
                           "add_channel_member", body, result)
            return result

    # --------------------------------------------------------------- messages

    def _serialize_message(self, row):
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "channel_id": row["channel_id"],
            "thread_id": row["thread_id"],
            "author": json.loads(row["author"]),
            "body": row["body"],
            "intent": row["intent"],
            "ticket_id": row["ticket_id"],
            "mentions": json.loads(row["mentions"]),
            "causation_id": row["causation_id"],
            "conversation_id": row["conversation_id"],
            "supersedes_message_id": row["supersedes_message_id"],
            "version": row["version"],
            "created_at": row["created_at"],
        }

    def _serialize_thread(self, row):
        return {
            "id": row["id"],
            "channel_id": row["channel_id"],
            "root_message_id": row["root_message_id"],
            "ticket_id": row["ticket_id"],
            "reply_count": row["reply_count"],
            "updated_at": row["updated_at"],
        }

    def _message_row(self, conn, project_id, message_id):
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            raise NotFound("No such message.", {"message_id": message_id})
        if row["project_id"] != project_id:
            raise ForbiddenScope(project_id, message_id)
        return row

    def _member_id_for_actor(self, conn, project_id, actor):
        if actor.get("type") == "system":
            return None
        if actor.get("type") == "agent":
            row = conn.execute(
                "SELECT id, state FROM members WHERE project_id = ? AND agent_id = ?",
                (project_id, actor.get("id")),
            ).fetchone()
            if row is None:
                raise ForbiddenScope(project_id, actor.get("id"))
            if row["state"] == "revoked":
                raise MembershipRevoked(row["id"])
            return row["id"]
        return actor.get("id")

    def _agent_recipients_for_mentions(self, conn, project_id, mentions):
        recipients = []
        for member_id in mentions:
            member = self._member_row(conn, project_id, member_id)
            if member["state"] == "revoked":
                continue
            if member["agent_id"] is not None:
                recipients.append(member["agent_id"])
        return recipients

    def _insert_delivery(self, conn, project_id, message_id, agent_id, *,
                         state="queued", reason=None, reason_detail=None,
                         run_id=None, blocking_ticket_id=None, attempts=1,
                         next_attempt_at=None, now=None):
        _assert_member(state, DELIVERY_STATES, "state")
        if reason is not None:
            _assert_member(reason, DELIVERY_REASONS, "reason")
        did = ids.delivery_id()
        at = now or ids.now()
        conn.execute(
            "INSERT INTO deliveries (id, project_id, message_id, recipient_agent_id,"
            " state, reason, reason_detail, run_id, blocking_ticket_id, attempts,"
            " next_attempt_at, created_at, updated_at, version)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (did, project_id, message_id, agent_id, state, reason, reason_detail,
             run_id, blocking_ticket_id, attempts, next_attempt_at, at, at),
        )
        return self._delivery_row(conn, project_id, did)

    def send_message(self, project_id, channel_id, author, body_text, *,
                     intent="message", thread_id=None, mentions=None,
                     ticket_id=None, causation_id=None, conversation_id=None,
                     supersedes_message_id=None, recipient_agent_ids=None,
                     message_id=None, request_id=None, delivery_state="queued"):
        """Persist a message and all outbox deliveries in one transaction.

        `delivery_state` is the state the outbox rows are born in. It defaults
        to `queued`, which is what ordinary conversation wants: nobody has been
        dispatched, and nothing is claimed to have been. `POST /messages/{id}/task`
        (T-187) passes `sent`, because a dispatched task has to be able to reach
        either `delivered` or `queued`-with-a-reason afterwards, and
        `DELIVERY_TRANSITIONS` allows both of those only from `sent`. Creating
        it as `queued` and transitioning would be illegal in both directions --
        which is why this is a parameter rather than a second write: the message
        and its outbox still land in one transaction.
        """
        _assert_member(intent, MESSAGE_INTENTS, "intent")
        if not isinstance(author, dict) or "type" not in author or "id" not in author:
            raise SenderIdentityRejected()
        mentions = sorted(mentions or [])
        recipients = sorted(set(recipient_agent_ids or []))
        mid = message_id or ids.message_id()
        body = {
            "channel_id": channel_id, "author": author, "body": body_text,
            "intent": intent, "thread_id": thread_id, "mentions": mentions,
            "ticket_id": ticket_id, "causation_id": causation_id,
            "conversation_id": conversation_id,
            "supersedes_message_id": supersedes_message_id,
            "recipient_agent_ids": list(recipients),
            "delivery_state": delivery_state,
        }
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id, "send_message", body)
            if replay is not None:
                return replay
            author_member_id = self._member_id_for_actor(conn, project_id, author)
            self._assert_channel_visible(conn, project_id, channel_id, author_member_id)
            if thread_id is not None:
                thread = conn.execute(
                    "SELECT channel_id FROM threads WHERE project_id = ? AND id = ?",
                    (project_id, thread_id),
                ).fetchone()
                if thread is None:
                    raise NotFound("No such thread.", {"thread_id": thread_id})
                if thread["channel_id"] != channel_id:
                    raise MalformedRequest("thread belongs to another channel.",
                                           {"thread_id": thread_id})
            for link_id, field in ((causation_id, "causation_id"),
                                   (supersedes_message_id, "supersedes_message_id")):
                if link_id is not None:
                    self._message_row(conn, project_id, link_id)
            recipients.extend(self._agent_recipients_for_mentions(conn, project_id, mentions))
            if author.get("type") == "agent":
                recipients = [r for r in recipients if r != author.get("id")]
            recipients = sorted(set(recipients))
            now = ids.now()
            conn.execute(
                "INSERT INTO messages (id, project_id, channel_id, thread_id, author,"
                " body, intent, ticket_id, mentions, causation_id, conversation_id,"
                " supersedes_message_id, version, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (mid, project_id, channel_id, thread_id, _json(author), body_text,
                 intent, ticket_id, _json(mentions), causation_id, conversation_id,
                 supersedes_message_id, now),
            )
            deliveries = [
                self._serialize_delivery(
                    self._insert_delivery(conn, project_id, mid, agent_id,
                                          state=delivery_state, now=now)
                )
                for agent_id in recipients
            ]
            if thread_id is not None:
                conn.execute(
                    "UPDATE threads SET reply_count = reply_count + 1, updated_at = ?"
                    " WHERE project_id = ? AND id = ?",
                    (now, project_id, thread_id),
                )
            self._audit(conn, project_id, author, "message.create",
                        subject_type="message", subject_id=mid,
                        request_id=request_id, summary="message created")
            result = {
                "message": self._serialize_message(
                    self._message_row(conn, project_id, mid)
                ),
                "deliveries": deliveries,
            }
            self._remember(conn, project_id, request_id, "send_message", body, result)
            return result

    def create_thread(self, project_id, channel_id, root_message_id, *,
                      ticket_id=None, thread_id=None):
        tid = thread_id or ids.thread_id()
        with write_txn(self.conn) as conn:
            channel = self._channel_row(conn, project_id, channel_id)
            message = self._message_row(conn, project_id, root_message_id)
            if message["channel_id"] != channel["id"]:
                raise MalformedRequest("root message belongs to another channel.",
                                       {"message_id": root_message_id})
            now = ids.now()
            conn.execute(
                "INSERT INTO threads (id, project_id, channel_id, root_message_id,"
                " ticket_id, reply_count, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?)",
                (tid, project_id, channel_id, root_message_id, ticket_id, now),
            )
            conn.execute(
                "UPDATE messages SET thread_id = ? WHERE project_id = ? AND id = ?",
                (tid, project_id, root_message_id),
            )
            self._audit(conn, project_id, self.SYSTEM_ACTOR, "thread.create",
                        subject_type="message", subject_id=tid,
                        summary="thread created")
            row = conn.execute("SELECT * FROM threads WHERE id = ?", (tid,)).fetchone()
            return self._serialize_thread(row)

    # Chronological rails tiebreak on `rowid`, not on `id`. `ids.now()` is
    # second-precision and message/delivery/thread ids are `secrets.choice`
    # random, so `ORDER BY created_at, id` puts two messages posted in the same
    # second in a *random* order -- a reply above the message it answers, and a
    # different order on every read of the same rows. `rowid` is insertion
    # order, which is the order the conversation actually happened in.
    def list_messages(self, project_id, channel_id, *, member_id=None, thread_id=None,
                      limit=50, cursor=None):
        """One page of a channel, oldest first, with a forward cursor.

        T-325/F1. This used to be `ORDER BY created_at, rowid LIMIT ?` with
        `next_cursor` hardcoded to None, which is not "no paging" -- it is a
        channel that silently freezes at its first `limit` messages while the
        payload asserts there is nothing further. At the contract maximum of
        200 that made message 201 onward unreachable through every parameter
        the contract offers.

        The cursor is a KEYSET on `(created_at, rowid)`, deliberately the same
        composite key the ordering above is built on and not an OFFSET. An
        offset pager would re-open the same-second hole this ordering exists to
        close: messages posted between two page reads shift every later row by
        one, so an offset either repeats or skips exactly the rows a busy
        channel is producing. A keyset asks "what comes strictly after this
        message", which stays true no matter what arrives meanwhile.
        """
        after = _decode_message_cursor(cursor)
        with read_txn(self.conn) as conn:
            self._assert_channel_visible(conn, project_id, channel_id, member_id)
            where = ["project_id = ?", "channel_id = ?"]
            params = [project_id, channel_id]
            if thread_id is not None:
                where.append("thread_id = ?")
                params.append(thread_id)
            if after is not None:
                # Row-value syntax ((a,b) > (?,?)) would say this in one clause,
                # but it needs SQLite 3.15+; written out, it works everywhere
                # and reads the same to the query planner.
                where.append("(created_at > ? OR (created_at = ? AND rowid > ?))")
                params.extend([after[0], after[0], after[1]])
            # One row past the page: the only honest way to know whether a next
            # page EXISTS. Emitting a cursor because the page came back full
            # would hand every exactly-divisible channel a cursor to an empty
            # page, and a client that trusts next_cursor would page forever.
            params.append(limit + 1)
            rows = conn.execute(
                "SELECT rowid AS _rowid, * FROM messages WHERE " + " AND ".join(where)
                + " ORDER BY created_at, rowid LIMIT ?",
                tuple(params),
            ).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            next_cursor = (
                _encode_message_cursor(rows[-1]["created_at"], rows[-1]["_rowid"])
                if has_more and rows else None
            )
            message_ids = [r["id"] for r in rows]
            if message_ids:
                marks = ",".join("?" for _ in message_ids)
                delivery_rows = conn.execute(
                    "SELECT * FROM deliveries WHERE project_id = ?"
                    " AND message_id IN ({}) ORDER BY created_at, rowid".format(marks),
                    (project_id, *message_ids),
                ).fetchall()
            else:
                delivery_rows = []
            thread_rows = conn.execute(
                "SELECT * FROM threads WHERE project_id = ? AND channel_id = ?"
                " ORDER BY updated_at, rowid",
                (project_id, channel_id),
            ).fetchall()
            payload = {
                "items": [self._serialize_message(r) for r in rows],
                "threads": [self._serialize_thread(r) for r in thread_rows],
                "deliveries": [self._serialize_delivery(r) for r in delivery_rows],
                "next_cursor": next_cursor,
                "stream": self._stream_status(conn, project_id),
            }
            if not rows:
                from ..screen_copy import EMPTY_STATES
                payload["empty_state"] = dict(EMPTY_STATES["messages"])
            return payload

    # -------------------------------------------------------------- deliveries

    def _delivery_row(self, conn, project_id, delivery_id):
        row = conn.execute("SELECT * FROM deliveries WHERE id = ?", (delivery_id,)).fetchone()
        if row is None:
            raise NotFound("No such delivery.", {"delivery_id": delivery_id})
        if row["project_id"] != project_id:
            raise ForbiddenScope(project_id, delivery_id)
        return row

    def _serialize_delivery(self, row):
        return {
            "id": row["id"],
            "message_id": row["message_id"],
            "recipient_agent_id": row["recipient_agent_id"],
            "state": row["state"],
            "reason": row["reason"],
            "reason_detail": row["reason_detail"],
            "run_id": row["run_id"],
            "blocking_ticket_id": row["blocking_ticket_id"],
            "attempts": row["attempts"],
            "next_attempt_at": row["next_attempt_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "version": row["version"],
        }

    def list_deliveries(self, project_id, message_id):
        rows = self.conn.execute(
            "SELECT * FROM deliveries WHERE project_id = ? AND message_id = ?"
            " ORDER BY created_at, rowid",
            (project_id, message_id),
        ).fetchall()
        return {"items": [self._serialize_delivery(r) for r in rows]}

    def transition_delivery(self, project_id, delivery_id, to_state, *,
                            expected_version, reason=None, reason_detail=None,
                            run_id=None, blocking_ticket_id=None,
                            next_attempt_at=None, request_id=None, actor=None):
        _assert_member(to_state, DELIVERY_STATES, "state")
        if reason is not None:
            _assert_member(reason, DELIVERY_REASONS, "reason")
        body = {
            "delivery_id": delivery_id, "to_state": to_state,
            "expected_version": expected_version, "reason": reason,
            "reason_detail": reason_detail, "run_id": run_id,
            "blocking_ticket_id": blocking_ticket_id,
            "next_attempt_at": next_attempt_at,
        }
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id,
                                  "transition_delivery", body)
            if replay is not None:
                return replay
            row = self._delivery_row(conn, project_id, delivery_id)
            if row["version"] != expected_version:
                raise TicketVersionConflict(delivery_id, expected_version, row["version"])
            if to_state not in DELIVERY_TRANSITIONS.get(row["state"], set()):
                raise InvalidStateTransition(delivery_id, row["state"], to_state)
            conn.execute(
                "UPDATE deliveries SET state = ?, reason = ?, reason_detail = ?,"
                " run_id = COALESCE(?, run_id), blocking_ticket_id = ?,"
                " next_attempt_at = ?, attempts = attempts + CASE WHEN ? = 'queued' THEN 1 ELSE 0 END,"
                " updated_at = ?, version = version + 1 WHERE id = ?",
                (to_state, reason, reason_detail, run_id, blocking_ticket_id,
                 next_attempt_at, to_state, ids.now(), delivery_id),
            )
            self._audit(conn, project_id, actor or self.SYSTEM_ACTOR,
                        "delivery.transition", subject_type="message",
                        subject_id=delivery_id, request_id=request_id,
                        summary="delivery {} -> {}".format(delivery_id, to_state))
            result = self._serialize_delivery(
                self._delivery_row(conn, project_id, delivery_id)
            )
            self._remember(conn, project_id, request_id,
                           "transition_delivery", body, result)
            return result

    # --------------------------------------------------------------- wake jobs

    def _wake_job_row(self, conn, project_id, wake_job_id):
        row = conn.execute("SELECT * FROM wake_jobs WHERE id = ?", (wake_job_id,)).fetchone()
        if row is None:
            raise NotFound("No such wake job.", {"wake_job_id": wake_job_id})
        if row["project_id"] != project_id:
            raise ForbiddenScope(project_id, wake_job_id)
        return row

    def _serialize_wake_job(self, row):
        return {
            "id": row["id"],
            "message_id": row["message_id"],
            "recipient_agent_id": row["recipient_agent_id"],
            "delivery_id": row["delivery_id"],
            "ticket_id": row["ticket_id"],
            "state": row["state"],
            "dedupe_key": row["dedupe_key"],
            "attempts": row["attempts"],
            "lease_expires_at": row["lease_expires_at"],
            "created_at": row["created_at"],
        }

    def create_wake_job(self, project_id, message_id, recipient_agent_id, delivery_id,
                        *, ticket_id=None, wake_job_id=None, request_id=None,
                        actor=None):
        wid = wake_job_id or ids.wake_job_id()
        dedupe_key = "{}:{}".format(message_id, recipient_agent_id)
        body = {
            "message_id": message_id, "recipient_agent_id": recipient_agent_id,
            "delivery_id": delivery_id, "ticket_id": ticket_id,
        }
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id,
                                  "create_wake_job", body)
            if replay is not None:
                return replay
            self._message_row(conn, project_id, message_id)
            self._delivery_row(conn, project_id, delivery_id)
            existing = conn.execute(
                "SELECT * FROM wake_jobs WHERE project_id = ? AND dedupe_key = ?",
                (project_id, dedupe_key),
            ).fetchone()
            if existing is not None:
                return self._serialize_wake_job(existing)
            now = ids.now()
            conn.execute(
                "INSERT INTO wake_jobs (id, project_id, message_id, recipient_agent_id,"
                " delivery_id, ticket_id, state, dedupe_key, attempts, lease_expires_at,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, 0, NULL, ?)",
                (wid, project_id, message_id, recipient_agent_id, delivery_id,
                 ticket_id, dedupe_key, now),
            )
            self._audit(conn, project_id, actor or self.SYSTEM_ACTOR,
                        "wake_job.create", subject_type="run", subject_id=wid,
                        request_id=request_id, summary="wake job created")
            result = self._serialize_wake_job(
                self._wake_job_row(conn, project_id, wid)
            )
            self._remember(conn, project_id, request_id,
                           "create_wake_job", body, result)
            return result

    def list_wake_jobs(self, project_id, *, recipient_agent_id=None):
        if recipient_agent_id is None:
            rows = self.conn.execute(
                "SELECT * FROM wake_jobs WHERE project_id = ?"
                " ORDER BY created_at, rowid",
                (project_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM wake_jobs WHERE project_id = ? AND recipient_agent_id = ?"
                " ORDER BY created_at, rowid",
                (project_id, recipient_agent_id),
            ).fetchall()
        return {"items": [self._serialize_wake_job(r) for r in rows],
                "poll_after_seconds": 0}


    # The runner is at-least-once by contract, so a wake job it never finished
    # has to become visible again. Two numbers govern that: how long a lease is
    # good for, and how many attempts a job gets before it stops being retried
    # and becomes inspectable instead. `docs/managed-runner.md` is where both
    # are justified; they are named here so nothing reads them from a literal.
    WAKE_LEASE_SECONDS = 120
    WAKE_MAX_ATTEMPTS = 3

    def _run_id_for_wake_job(self, wake_job_id):
        """The run a wake job mints, derived rather than looked up.

        `WakeJobListResponse` is `additionalProperties: false` over
        `[items, poll_after_seconds]`, so the frozen contract gives the leasing
        response nowhere to carry the run it just created -- and there is no
        `GET /runs` or `POST /runs` for the runner to find it with either. The
        run id is therefore a pure function of the wake job id: `wjb_abcd1234`
        mints `run_abcd1234`. Both patterns are `_[0-9a-z]{8,32}`, so the
        derived id is a valid `RunId`, and the runner computes it locally
        without a round trip. Raised as a contract gap in docs/api-notes.md;
        deriving it is the non-amending way to close it.
        """
        return "run_" + wake_job_id[4:]

    def _reclaim_expired(self, conn, project_id, agent_id, now):
        """Take back what a dead supervisor was holding -- and only retry what
        it is safe to retry.

        The distinction is the whole point, and it is the design doc's rule
        about uncertain external side effects made concrete:

        - the run never left `pending`: nothing was spawned, so nothing outside
          the board happened. The job goes back to the queue and the same run
          is reused on the next attempt. This is an honest retry.
        - the run reached `starting` or further: a Claude session was retained
          and probably a process was started. Whether it edited files, pushed a
          commit or replied to anybody is exactly what nobody can now know. So
          the run is FAILED with that stated, the job is failed with it, and it
          is not retried. Re-running a task that may have half-finished is
          worse than telling a human it is unclear -- and "at-least-once
          delivery" was never a licence to execute twice.
        """
        rows = conn.execute(
            "SELECT * FROM wake_jobs WHERE project_id = ? AND recipient_agent_id = ?"
            " AND state = 'leased' AND lease_expires_at IS NOT NULL"
            " AND lease_expires_at <= ?",
            (project_id, agent_id, now),
        ).fetchall()
        for job in rows:
            run = conn.execute(
                "SELECT * FROM runs WHERE wake_job_id = ?", (job["id"],)
            ).fetchone()
            if run is not None and run["state"] == "paused":
                # `paused` is `needs_approval` or `budget_reached`: the run is
                # waiting for a person, deliberately, and reaping it as an
                # orphan would quietly discard the approval the pause exists to
                # get. It keeps its lease on the agent until an operator
                # cancels or resumes it -- moving on to the next job instead
                # would be a way to bypass an approval by waiting.
                continue
            if run is not None and run["state"] not in ("pending",):
                if run["state"] not in ("responded", "canceled", "failed"):
                    reason = ("The runner lease expired while this run was in "
                              "flight. Its effects are uncertain; it needs "
                              "review and was not retried.")
                    conn.execute(
                        "UPDATE runs SET state = 'failed', terminal_reason = ?,"
                        " ended_at = ?, version = version + 1 WHERE id = ?",
                        (reason, now, run["id"]),
                    )
                    self._audit(conn, project_id, self.SYSTEM_ACTOR,
                                "run.event.orphaned", subject_type="run",
                                subject_id=run["id"], summary=reason)
                conn.execute(
                    "UPDATE wake_jobs SET state = 'failed', lease_expires_at = NULL"
                    " WHERE id = ?", (job["id"],))
                self._audit(conn, project_id, self.SYSTEM_ACTOR,
                            "wake_job.failed", subject_type="run",
                            subject_id=job["id"],
                            summary="wake job orphaned by an expired runner"
                                    " lease after its run had started")
                continue
            conn.execute(
                "UPDATE wake_jobs SET state = 'pending', lease_expires_at = NULL"
                " WHERE id = ?", (job["id"],))
            self._audit(conn, project_id, self.SYSTEM_ACTOR,
                        "wake_job.reclaimed", subject_type="run",
                        subject_id=job["id"],
                        summary="lease expired before the run started;"
                                " requeued for another attempt")

    def _agent_is_busy(self, conn, project_id, agent_id):
        """Is a supervisor working this agent right now?

        Two different facts, and taking only the first is the bug that cost an
        afternoon here. A run in `starting`/`running`/`paused` is obviously
        busy. But a run in `pending` is *not*: `pending` is the state a run is
        minted in at lease time and reset to when a dead supervisor's lease is
        reclaimed, so treating it as busy deadlocks the agent -- the job goes
        back to the queue and can never be handed out again, because the run it
        already minted keeps saying somebody is on it.

        What actually says "somebody is on it" for a not-yet-started run is the
        wake job's own lease. So: a non-pending run, or a job still leased.
        """
        run = conn.execute(
            "SELECT 1 FROM runs WHERE project_id = ? AND recipient_agent_id = ?"
            " AND state IN ('starting', 'running', 'paused') LIMIT 1",
            (project_id, agent_id),
        ).fetchone()
        if run is not None:
            return True
        return conn.execute(
            "SELECT 1 FROM wake_jobs WHERE project_id = ? AND recipient_agent_id = ?"
            " AND state = 'leased' LIMIT 1",
            (project_id, agent_id),
        ).fetchone() is not None

    def _assert_runner_lease(self, conn, project_id, agent_id, runner_id, epoch, now):
        row = conn.execute(
            "SELECT * FROM runner_leases WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row is None or row["project_id"] != project_id:
            raise RunAlreadyActive(agent_id)
        if row["runner_id"] != runner_id or row["epoch"] != epoch \
                or row["expires_at"] <= now:
            raise RunAlreadyActive(agent_id, row["runner_id"], row["epoch"])
        return row

    def lease_wake_jobs(self, project_id, agent_id, runner_id, *, epoch,
                        lease_seconds=None, at=None):
        """Hand a registered runner the work it may start, and nothing else.

        Four rules, all of them enforced here rather than trusted to the
        supervisor, because a supervisor that has crashed or been superseded is
        exactly the one that will not enforce them:

        - **Fencing.** Only the runner named in the current, unexpired lease at
          the epoch it was issued gets jobs. Anyone else gets 409
          `run_already_active` -- a second supervisor for the same agent must
          not start a parallel session.
        - **Concurrency 1.** While this agent has a non-terminal run, no new job
          is leased. The job stays `pending` and is picked up when the run ends.
          That is the design doc's "new tasks queue behind its current claim",
          and it is why a duplicate delivery cannot produce a second live run.
        - **At-least-once, bounded.** A lease that expires without the job being
          completed returns the job to `pending` for another attempt. After
          `WAKE_MAX_ATTEMPTS` it goes to `failed` instead of retrying forever --
          the inspectable failed queue, not a silent drop and not a hot loop.
        - **The run exists before the spawn.** Leasing mints the run (id derived
          from the wake job) so a crash between claim and spawn leaves a record
          to reconcile against. Re-leasing the same job returns the same run.

        This is not idempotency-keyed: leasing is a read-and-take, and the
        contract's `GET /runners/jobs` carries no request_id to key it with.
        Re-delivery is handled by dedupe on the job, not by replay on the call.
        """
        lease_seconds = self.WAKE_LEASE_SECONDS if lease_seconds is None else lease_seconds
        with write_txn(self.conn) as conn:
            now = at or ids.now()
            self._assert_runner_lease(conn, project_id, agent_id, runner_id,
                                      epoch, now)

            # Reclaim what a dead supervisor was holding, then retire whatever
            # has now burned its attempts. Order matters: a job whose lease just
            # expired on its last attempt should fail, not be handed out again.
            self._reclaim_expired(conn, project_id, agent_id, now)
            retired = conn.execute(
                "SELECT id FROM wake_jobs WHERE project_id = ? AND recipient_agent_id = ?"
                " AND state = 'pending' AND attempts >= ?",
                (project_id, agent_id, self.WAKE_MAX_ATTEMPTS),
            ).fetchall()
            for row in retired:
                conn.execute("UPDATE wake_jobs SET state = 'failed' WHERE id = ?",
                             (row["id"],))
                self._audit(conn, project_id, self.SYSTEM_ACTOR, "wake_job.failed",
                            subject_type="run", subject_id=row["id"],
                            summary="wake job exhausted {} attempts; moved to the"
                                    " failed queue".format(self.WAKE_MAX_ATTEMPTS))

            if self._agent_is_busy(conn, project_id, agent_id):
                return {"items": [], "poll_after_seconds": 1}

            # `ORDER BY created_at, rowid`, not `created_at, id`. Timestamps
            # here are second-granular and real traffic bursts inside one
            # second -- T-181 lost a live session to exactly this, where
            # per-second event ids collided under a few tool calls a second.
            # Ordering the tie by the random `wjb_...` id makes the wake queue
            # silently non-FIFO: two task messages sent in the same second get
            # executed in whichever order their ids happened to sort. `rowid`
            # is insertion order and has no ties.
            # Exactly one. `RunnerLease.concurrency` is `minimum: 1, maximum: 1`
            # in the frozen contract, so there is no configuration under which
            # a second job is eligible here -- leasing one makes the agent busy
            # by definition. A `limit` parameter would only be a way to write a
            # number that could never take effect.
            row = conn.execute(
                "SELECT * FROM wake_jobs WHERE project_id = ? AND recipient_agent_id = ?"
                " AND state = 'pending' ORDER BY created_at, rowid LIMIT 1",
                (project_id, agent_id),
            ).fetchone()
            if row is None:
                return {"items": [], "poll_after_seconds": 1}
            conn.execute(
                "UPDATE wake_jobs SET state = 'leased', attempts = attempts + 1,"
                " lease_expires_at = ? WHERE id = ?",
                (ids.in_seconds(lease_seconds, at=now), row["id"]),
            )
            self._mint_run_for(conn, project_id, agent_id, row, now)
            self._audit(conn, project_id, self.SYSTEM_ACTOR, "wake_job.leased",
                        subject_type="run", subject_id=row["id"],
                        summary="wake job leased to runner {}".format(runner_id))
            return {"items": [self._serialize_wake_job(
                        self._wake_job_row(conn, project_id, row["id"]))],
                    "poll_after_seconds": 0}

    def _mint_run_for(self, conn, project_id, agent_id, wake_row, now):
        run_id = self._run_id_for_wake_job(wake_row["id"])
        existing = conn.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
        if existing is not None:
            return run_id
        conn.execute(
            "INSERT INTO runs (id, project_id, recipient_agent_id, session_id,"
            " wake_job_id, ticket_claim, state, terminal_reason, budget,"
            " needs_approval, started_at, ended_at, created_at, version)"
            " VALUES (?, ?, ?, NULL, ?, ?, 'pending', NULL, ?, 0, NULL, NULL, ?, 1)",
            (run_id, project_id, agent_id, wake_row["id"], wake_row["ticket_id"],
             _json(_budget(self._lease_budget(conn, agent_id))), now),
        )
        self._audit(conn, project_id, self.SYSTEM_ACTOR, "run.create",
                    subject_type="run", subject_id=run_id,
                    summary="run minted for wake job {}".format(wake_row["id"]))
        return run_id

    def _lease_budget(self, conn, agent_id):
        row = conn.execute(
            "SELECT budget FROM runner_leases WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        return json.loads(row["budget"]) if row is not None else None

    def complete_wake_job(self, project_id, wake_job_id, state, *, agent_id=None,
                          request_id=None):
        """Close a leased job out. `completed`, `failed` or `canceled` only.

        The runner calls this after the run reaches a terminal state. Leaving it
        `leased` is also safe -- the lease expires and the job is retried -- but
        that costs a whole lease window and one of the job's bounded attempts,
        so a supervisor that knows the answer says so.
        """
        if state not in {"completed", "failed", "canceled"}:
            raise MalformedRequest("wake job state is not terminal.",
                                   {"rejected_fields": ["state"]})
        body = {"wake_job_id": wake_job_id, "state": state}
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id,
                                  "complete_wake_job", body)
            if replay is not None:
                return replay
            row = self._wake_job_row(conn, project_id, wake_job_id)
            if agent_id is not None and row["recipient_agent_id"] != agent_id:
                raise ForbiddenScope(project_id, wake_job_id)
            conn.execute(
                "UPDATE wake_jobs SET state = ?, lease_expires_at = NULL WHERE id = ?",
                (state, wake_job_id),
            )
            self._audit(conn, project_id, self.SYSTEM_ACTOR,
                        "wake_job.{}".format(state), subject_type="run",
                        subject_id=wake_job_id, request_id=request_id,
                        summary="wake job {}".format(state))
            result = self._serialize_wake_job(
                self._wake_job_row(conn, project_id, wake_job_id))
            self._remember(conn, project_id, request_id, "complete_wake_job",
                           body, result)
            return result

    def _close_wake_job_for_run(self, conn, project_id, wake_job_id, state):
        if not wake_job_id:
            return
        conn.execute(
            "UPDATE wake_jobs SET state = ?, lease_expires_at = NULL"
            " WHERE id = ? AND project_id = ? AND state IN ('pending', 'leased')",
            (state, wake_job_id, project_id),
        )

    def get_run(self, project_id, run_id):
        return self._serialize_run(self._run_row(self.conn, project_id, run_id))

    # ------------------------------------------------------------------- runs

    def _run_row(self, conn, project_id, run_id):
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFound("No such run.", {"run_id": run_id})
        if row["project_id"] != project_id:
            raise ForbiddenScope(project_id, run_id)
        return row

    def _serialize_run(self, row):
        return {
            "id": row["id"],
            "recipient_agent_id": row["recipient_agent_id"],
            "session_id": row["session_id"],
            "wake_job_id": row["wake_job_id"],
            "ticket_claim": row["ticket_claim"],
            "state": row["state"],
            "terminal_reason": row["terminal_reason"],
            "budget": json.loads(row["budget"]),
            "needs_approval": bool(row["needs_approval"]),
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "created_at": row["created_at"],
            "version": row["version"],
        }

    def create_run(self, project_id, recipient_agent_id, *, session_id=None,
                   wake_job_id=None, ticket_claim=None, state="pending",
                   budget=None, run_id=None, request_id=None):
        _assert_member(state, RUN_STATES, "state")
        rid = run_id or ids.run_id()
        body = {
            "recipient_agent_id": recipient_agent_id, "session_id": session_id,
            "wake_job_id": wake_job_id, "ticket_claim": ticket_claim,
            "state": state, "budget": _budget(budget),
        }
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id, "create_run", body)
            if replay is not None:
                return replay
            agent = conn.execute(
                "SELECT project_id FROM agents WHERE id = ?", (recipient_agent_id,)
            ).fetchone()
            if agent is None:
                raise NotFound("No such agent.", {"agent_id": recipient_agent_id})
            if agent["project_id"] != project_id:
                raise ForbiddenScope(project_id, recipient_agent_id)
            if wake_job_id is not None:
                self._wake_job_row(conn, project_id, wake_job_id)
            now = ids.now()
            conn.execute(
                "INSERT INTO runs (id, project_id, recipient_agent_id, session_id,"
                " wake_job_id, ticket_claim, state, terminal_reason, budget,"
                " needs_approval, started_at, ended_at, created_at, version)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 0, NULL, NULL, ?, 1)",
                (rid, project_id, recipient_agent_id, session_id, wake_job_id,
                 ticket_claim, state, _json(_budget(budget)), now),
            )
            self._audit(conn, project_id, self.SYSTEM_ACTOR, "run.create",
                        subject_type="run", subject_id=rid,
                        request_id=request_id, summary="run created")
            result = self._serialize_run(self._run_row(conn, project_id, rid))
            self._remember(conn, project_id, request_id, "create_run", body, result)
            return result

    def record_run_event(self, project_id, run_id, event, *, expected_version,
                         reporter_session_id=None, session_id=None,
                         ticket_claim=None, reason=None, budget=None,
                         request_id=None):
        """Apply one run event -- but only for a reporter that is attributed.

        Two different sessions meet on this call, and conflating them is the
        whole hazard, so they are named apart:

        - `reporter_session_id` is the SUPERVISOR's runtime session. It is
          bound from the agent credential in `BoardServer.post_run_event` and
          is never read from the request body. It is the identity claim, and
          frozen T-178 (actor bound from the credential) governs it.
        - `session_id` is the CHILD session this run executes in, retained
          before the launch so a crash between claim and spawn reconciles
          against any existing local process. No credential can carry it -- it
          does not exist until the supervisor mints it -- so it arrives in the
          body and is bound a different way: write-once.

        T-239's three-state rule, applied here to the reporter:

        - attributed (a live, unrevoked supervisor session, and a child session
          that either matches the retained one or is the first) -> applies,
          audited `run.event`
        - superseded (a revoked session, or a child session that contradicts
          the one this run already retained) -> recorded, denied effect,
          audited `run.event.superseded`
        - unattributed (an agent token carrying no session at all) -> recorded,
          denied effect, audited `run.event.unattributed`

        Denied effect is total for the run's liveness: no state change, no
        started_at/ended_at, no needs_approval, no terminal_reason, no budget
        counters, no session adoption and no version bump -- so an attributed
        reporter's `expected_version` still holds afterwards. The point is that
        an anonymous caller must not be able to paint a run green
        (`started` -> running) or close it (`responded`), which is exactly what
        omitting an optional identity field buys when absence reads as ordinary.

        A supervisor that genuinely lost its session does not recover by
        closing the run anonymously. It recovers through the runner lease: the
        lease expires, a supervisor registers at a new epoch, and reconciles.
        """
        if event not in RUN_EVENT_TARGETS:
            raise MalformedRequest("event is not a valid run event.", {"event": event})
        body = {
            "run_id": run_id, "event": event, "expected_version": expected_version,
            "reporter_session_id": reporter_session_id,
            "session_id": session_id, "ticket_claim": ticket_claim,
            "reason": reason, "budget": budget,
        }
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id, "record_run_event", body)
            if replay is not None:
                return replay
            row = self._run_row(conn, project_id, run_id)
            if row["version"] != expected_version:
                raise TicketVersionConflict(run_id, expected_version, row["version"])
            if row["state"] in {"responded", "canceled", "failed"}:
                raise InvalidStateTransition(run_id, row["state"],
                                             RUN_EVENT_TARGETS[event])
            # Ordered ahead of the attribution gate on purpose. The contract
            # names 422 `invalid_state_transition` for "started with no runtime
            # session", and that answer should not change depending on who is
            # asking -- an unattributed caller in this position would otherwise
            # get a 200 where every other caller gets the documented refusal.
            if event == "started" and session_id is None and row["session_id"] is None:
                raise InvalidStateTransition(run_id, row["state"], "running")

            attributed = reporter_session_id is not None and \
                self._session_is_current(conn, reporter_session_id)
            contradicts_child = (
                session_id is not None
                and row["session_id"] is not None
                and session_id != row["session_id"]
            )
            if not attributed or contradicts_child:
                if reporter_session_id is None:
                    action = "run.event.unattributed"
                    summary = ("run event {} with no reporter session;"
                               " not attributed, run unchanged".format(event))
                else:
                    action = "run.event.superseded"
                    summary = ("run event {} from a superseded session;"
                               " denied effect, run unchanged".format(event))
                self._audit(conn, project_id, self.SYSTEM_ACTOR, action,
                            subject_type="run", subject_id=run_id,
                            request_id=request_id, summary=summary)
                result = self._serialize_run(row)
                self._remember(conn, project_id, request_id,
                               "record_run_event", body, result)
                return result

            now = ids.now()
            new_state = RUN_EVENT_TARGETS[event]
            started_at = row["started_at"]
            ended_at = row["ended_at"]
            needs_approval = row["needs_approval"]
            terminal_reason = row["terminal_reason"]
            if event == "started":
                started_at = started_at or now
            elif event == "needs_approval":
                needs_approval = 1
                terminal_reason = reason or "Needs approval."
            elif event in {"responded", "failed"}:
                ended_at = now
                terminal_reason = reason
            elif event == "budget_reached":
                terminal_reason = reason or "Run budget reached."
            next_budget = _budget(json.loads(row["budget"]))
            if budget:
                next_budget.update(budget)
            conn.execute(
                "UPDATE runs SET session_id = COALESCE(?, session_id),"
                " ticket_claim = COALESCE(?, ticket_claim), state = ?,"
                " terminal_reason = ?, budget = ?, needs_approval = ?,"
                " started_at = ?, ended_at = ?, version = version + 1"
                " WHERE id = ?",
                (session_id, ticket_claim, new_state, terminal_reason,
                 _json(next_budget), needs_approval, started_at, ended_at, run_id),
            )
            if new_state in {"responded", "failed"}:
                # The job that minted this run is done with. Left `leased` it
                # would be reclaimed on lease expiry and the same task run a
                # second time -- at-least-once delivery is not a licence to
                # execute twice. Same transaction as the state change, so there
                # is no window where the run is closed and the job is not.
                self._close_wake_job_for_run(
                    conn, project_id, row["wake_job_id"],
                    "completed" if new_state == "responded" else "failed")
            self._audit(conn, project_id, self.SYSTEM_ACTOR, "run.event",
                        subject_type="run", subject_id=run_id,
                        request_id=request_id,
                        summary="run event {}".format(event))
            result = self._serialize_run(self._run_row(conn, project_id, run_id))
            self._remember(conn, project_id, request_id,
                           "record_run_event", body, result)
            return result

    def cancel_run(self, project_id, run_id, *, expected_version, reason,
                   request_id=None):
        body = {"run_id": run_id, "expected_version": expected_version,
                "reason": reason}
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id, "cancel_run", body)
            if replay is not None:
                return replay
            row = self._run_row(conn, project_id, run_id)
            if row["version"] != expected_version:
                raise TicketVersionConflict(run_id, expected_version, row["version"])
            if row["state"] in {"responded", "canceled", "failed"}:
                raise InvalidStateTransition(run_id, row["state"], "canceled")
            conn.execute(
                "UPDATE runs SET state = 'canceled', terminal_reason = ?,"
                " ended_at = ?, version = version + 1 WHERE id = ?",
                (reason, ids.now(), run_id),
            )
            self._close_wake_job_for_run(conn, project_id, row["wake_job_id"],
                                         "canceled")
            self._audit(conn, project_id, self.SYSTEM_ACTOR, "run.cancel",
                        subject_type="run", subject_id=run_id,
                        request_id=request_id, summary="run canceled")
            result = self._serialize_run(self._run_row(conn, project_id, run_id))
            self._remember(conn, project_id, request_id, "cancel_run", body, result)
            return result

    # ----------------------------------------------------------- runner leases

    def _serialize_runner_lease(self, row):
        return {
            "runner_id": row["runner_id"],
            "agent_id": row["agent_id"],
            "epoch": row["epoch"],
            "acquired_at": row["acquired_at"],
            "expires_at": row["expires_at"],
            "allowlisted_worktree": row["allowlisted_worktree"],
            "runtime_profile": row["runtime_profile"],
            "permission_policy": row["permission_policy"],
            "concurrency": row["concurrency"],
            "budget": json.loads(row["budget"]),
        }

    def get_runner_lease(self, project_id, agent_id):
        row = self.conn.execute(
            "SELECT * FROM runner_leases WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            raise NotFound("No such runner lease.", {"agent_id": agent_id})
        if row["project_id"] != project_id:
            raise ForbiddenScope(project_id, agent_id)
        return self._serialize_runner_lease(row)

    def acquire_runner_lease(self, project_id, runner_id, agent_id, expires_at, *,
                             allowlisted_worktree, runtime_profile="claude-code-default",
                             permission_policy="prompt", expected_epoch=None,
                             budget=None, at=None, request_id=None,
                             worktree_source=None):
        _assert_member(permission_policy, {"prompt", "allowlist", "deny_all"},
                       "permission_policy")
        body = {
            "runner_id": runner_id, "agent_id": agent_id, "expires_at": expires_at,
            "allowlisted_worktree": allowlisted_worktree,
            "runtime_profile": runtime_profile,
            "permission_policy": permission_policy,
            "expected_epoch": expected_epoch,
            "budget": _budget(budget),
        }
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id,
                                  "acquire_runner_lease", body)
            if replay is not None:
                return replay
            agent = conn.execute(
                "SELECT project_id FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
            if agent is None:
                raise NotFound("No such agent.", {"agent_id": agent_id})
            if agent["project_id"] != project_id:
                raise ForbiddenScope(project_id, agent_id)
            now = at or ids.now()
            row = conn.execute(
                "SELECT * FROM runner_leases WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if row is None:
                if expected_epoch not in (None, 0):
                    raise RunAlreadyActive(agent_id, epoch=0)
                epoch = 1
                conn.execute(
                    "INSERT INTO runner_leases (agent_id, project_id, runner_id, epoch,"
                    " acquired_at, expires_at, allowlisted_worktree, runtime_profile,"
                    " permission_policy, concurrency, budget)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                    (agent_id, project_id, runner_id, epoch, now, expires_at,
                     allowlisted_worktree, runtime_profile, permission_policy,
                     _json(_budget(budget))),
                )
            elif row["expires_at"] <= now:
                epoch = row["epoch"] + 1
                conn.execute(
                    "UPDATE runner_leases SET runner_id = ?, epoch = ?, acquired_at = ?,"
                    " expires_at = ?, allowlisted_worktree = ?, runtime_profile = ?,"
                    " permission_policy = ?, concurrency = 1, budget = ?"
                    " WHERE agent_id = ?",
                    (runner_id, epoch, now, expires_at, allowlisted_worktree,
                     runtime_profile, permission_policy, _json(_budget(budget)),
                     agent_id),
                )
            elif row["runner_id"] == runner_id and expected_epoch == row["epoch"]:
                conn.execute(
                    "UPDATE runner_leases SET expires_at = ?, allowlisted_worktree = ?,"
                    " runtime_profile = ?, permission_policy = ?, budget = ?"
                    " WHERE agent_id = ?",
                    (expires_at, allowlisted_worktree, runtime_profile,
                     permission_policy, _json(_budget(budget)), agent_id),
                )
            else:
                raise RunAlreadyActive(agent_id, row["runner_id"], row["epoch"])
            # T-485/A1: `worktree_source` says whether the allowlisted
            # directory on this lease is one an OPERATOR approved at enrolment
            # or one the RUNNER asked for and was not refused. It is a label,
            # not a permission -- it changes nothing that is stored and is
            # deliberately kept out of `body`, which is the replay dedupe key.
            summary = "runner lease acquired"
            if worktree_source is not None:
                summary += " (worktree {}-supplied)".format(worktree_source)
            self._audit(conn, project_id, self.SYSTEM_ACTOR, "runner_lease.acquire",
                        subject_type="run", subject_id=agent_id,
                        request_id=request_id, summary=summary)
            result = self._serialize_runner_lease(
                conn.execute(
                    "SELECT * FROM runner_leases WHERE agent_id = ?", (agent_id,)
                ).fetchone()
            )
            self._remember(conn, project_id, request_id,
                           "acquire_runner_lease", body, result)
            return result

    def heartbeat_runner_lease(self, project_id, runner_id, agent_id, expires_at, *,
                               expected_epoch, at=None, request_id=None):
        body = {"runner_id": runner_id, "agent_id": agent_id,
                "expires_at": expires_at, "expected_epoch": expected_epoch}
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id,
                                  "heartbeat_runner_lease", body)
            if replay is not None:
                return replay
            row = conn.execute(
                "SELECT * FROM runner_leases WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            now = at or ids.now()
            if row is None or row["project_id"] != project_id:
                raise RunAlreadyActive(agent_id)
            if row["runner_id"] != runner_id or row["epoch"] != expected_epoch \
                    or row["expires_at"] <= now:
                raise RunAlreadyActive(agent_id, row["runner_id"], row["epoch"])
            conn.execute(
                "UPDATE runner_leases SET expires_at = ? WHERE agent_id = ?",
                (expires_at, agent_id),
            )
            self._audit(conn, project_id, self.SYSTEM_ACTOR, "runner_lease.heartbeat",
                        subject_type="run", subject_id=agent_id,
                        request_id=request_id, summary="runner lease heartbeat")
            result = self._serialize_runner_lease(
                conn.execute(
                    "SELECT * FROM runner_leases WHERE agent_id = ?", (agent_id,)
                ).fetchone()
            )
            self._remember(conn, project_id, request_id,
                           "heartbeat_runner_lease", body, result)
            return result

    def expire_runner_lease(self, project_id, runner_id, agent_id, *, expected_epoch,
                            at=None, request_id=None):
        now = at or ids.now()
        body = {"runner_id": runner_id, "agent_id": agent_id,
                "expected_epoch": expected_epoch, "expires_at": now}
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id,
                                  "expire_runner_lease", body)
            if replay is not None:
                return replay
            row = conn.execute(
                "SELECT * FROM runner_leases WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if row is None or row["project_id"] != project_id:
                raise RunAlreadyActive(agent_id)
            if row["runner_id"] != runner_id or row["epoch"] != expected_epoch:
                raise RunAlreadyActive(agent_id, row["runner_id"], row["epoch"])
            conn.execute(
                "UPDATE runner_leases SET expires_at = ? WHERE agent_id = ?",
                (now, agent_id),
            )
            self._audit(conn, project_id, self.SYSTEM_ACTOR, "runner_lease.expire",
                        subject_type="run", subject_id=agent_id,
                        request_id=request_id, summary="runner lease expired")
            result = self._serialize_runner_lease(
                conn.execute(
                    "SELECT * FROM runner_leases WHERE agent_id = ?", (agent_id,)
                ).fetchone()
            )
            self._remember(conn, project_id, request_id,
                           "expire_runner_lease", body, result)
            return result

    # ---------------------------------------------------------------- stream

    def _stream_status(self, conn, project_id):
        row = conn.execute(
            "SELECT seq, event_id, occurred_at FROM audit_events WHERE project_id = ?"
            " ORDER BY seq DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if row is None:
            return {"state": "live", "snapshot_version": 0,
                    "as_of": ids.now(), "last_event_id": None}
        return {"state": "live", "snapshot_version": row["seq"],
                "as_of": row["occurred_at"], "last_event_id": row["event_id"]}


def _encode_message_cursor(created_at, rowid):
    """Opaque by contract (`maxLength: 512`); the encoding is ours to change.

    Deliberately the same base64-of-a-typed-pair shape as
    `server/views.encode_cursor`, and deliberately NOT an import of it: storage
    must not depend on the server package. The `k` tag is what stops a cursor
    from one listing being replayed against another and quietly returning a
    page from the wrong rail.
    """
    raw = json.dumps({"k": "messages", "v": [created_at, rowid]},
                     separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_message_cursor(cursor):
    """(created_at, rowid), or None for no cursor.

    A cursor that does not decode is refused, never ignored: silently serving
    page 1 for a corrupt cursor is exactly the defect this replaces -- a client
    that pages sees a plausible response and loops forever.
    """
    if cursor is None or cursor == "":
        return None
    if not isinstance(cursor, str):
        raise MalformedRequest("cursor must be a string.",
                               {"rejected_fields": ["cursor"]})
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        parsed = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if parsed.get("k") != "messages":
            raise ValueError("cursor is for a different listing")
        created_at, rowid = parsed["v"]
        if not isinstance(created_at, str) or not isinstance(rowid, int):
            raise ValueError("cursor payload has the wrong shape")
    except Exception:
        raise MalformedRequest("cursor is not valid for this listing.",
                               {"rejected_fields": ["cursor"]})
    return (created_at, rowid)
