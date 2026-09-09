"""SSE: durable cursor, replay, snapshot fallback and heartbeat.

The audit trail *is* the stream. T-179 mints `event_id` as
`evt_<12-digit seq>_<6>`, zero-padded so lexicographic order matches sequence
order, which is what makes `Last-Event-ID` a correct resume point rather than a
hint. Nothing here keeps a second, parallel notion of ordering; the sequence is
read back out of the id.

Three behaviours the contract calls out, and why each is written the way it is:

* **Replay** starts at the event *after* the id the client sends, so a client
  that applied its last frame does not re-apply it.
* **`snapshot_required`** is emitted when the client's id is older than the
  retention window. Silently starting from head would leave the dashboard
  showing a state that no event will ever correct; one explicit frame tells it
  to re-read the screen instead.
* **`heartbeat`** frames carry no SSE `id:` line. They exist so a dead
  connection is distinguishable from an idle one, and they are not replayable
  positions -- giving them an id would move the client's cursor to an event
  that no replay can start from.
"""

from __future__ import annotations

import json
import time

from ..storage import ids
from .views import SSE_RETENTION_EVENTS, head_event, serialize_agent, \
    serialize_assignment, serialize_master_lease

# audit action prefix -> StreamEnvelope.type. Anything unmatched is
# `audit_appended`, which is a real member of the enum and not a fallback we
# invented: an event the console does not model still belongs in the trail.
_TYPE_BY_PREFIX = (
    ("ticket.", "ticket_changed"),
    ("review.", "review_changed"),
    ("assignment.", "assignment_changed"),
    ("master.", "master_lease_changed"),
    ("agent.", "agent_changed"),
    ("session.", "agent_changed"),
    ("hook.", "agent_changed"),
)

HEARTBEAT_SECONDS = 15
POLL_SECONDS = 0.25


def seq_of(event_id):
    """Recover the sequence number from a stream id.

    This is the property the handoff asks callers to preserve: the id is not
    opaque, it is a zero-padded sequence, and resume correctness depends on it.
    """
    try:
        return int(event_id.split("_")[1])
    except (AttributeError, IndexError, ValueError):
        return None


def event_type(action):
    for prefix, kind in _TYPE_BY_PREFIX:
        if action.startswith(prefix):
            return kind
    return "audit_appended"


def encode_frame(envelope, *, with_id=True):
    """One SSE frame. `event:` carries the type so a client can switch on it."""
    lines = []
    if with_id and envelope.get("event_id"):
        lines.append("id: {}".format(envelope["event_id"]))
    lines.append("event: {}".format(envelope["type"]))
    lines.append("data: {}".format(json.dumps(envelope, separators=(",", ":"))))
    lines.append("")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


_UNRESOLVED_CHANNEL = object()


class EventStream:
    def __init__(self, store, *, retention=SSE_RETENTION_EVENTS,
                 heartbeat_seconds=HEARTBEAT_SECONDS, poll_seconds=POLL_SECONDS):
        self.store = store
        self.retention = retention
        self.heartbeat_seconds = heartbeat_seconds
        self.poll_seconds = poll_seconds

    # ------------------------------------------------------------- envelopes

    def envelope(self, audit_row, snapshot_version, principal):
        kind = event_type(audit_row["action"])
        return {
            "event_id": audit_row["event_id"],
            "type": kind,
            "snapshot_version": snapshot_version,
            "occurred_at": audit_row["occurred_at"],
            "subject_id": audit_row.get("subject_id"),
            "payload": self._payload(kind, audit_row, principal),
            "resume_hint": None,
        }

    def control(self, kind, snapshot_version, *, reason=None):
        """A `heartbeat` or `snapshot_required` frame.

        Its `event_id` is minted at the current snapshot version so the
        frame validates as a StreamEnvelope, but it is never written as an
        SSE `id:` -- see `frames`.
        """
        return {
            "event_id": ids.event_id(snapshot_version),
            "type": kind,
            "snapshot_version": snapshot_version,
            "occurred_at": ids.now(),
            "subject_id": None,
            "payload": None,
            "resume_hint": {"reason": reason} if reason else None,
        }

    def _payload(self, kind, audit_row, principal):
        """The changed record, or None when it cannot be resolved.

        A deleted or unreadable subject yields `null` rather than an error: the
        stream must not die because one row went away between the audit write
        and the read.
        """
        project_id = audit_row["project_id"]
        subject = audit_row.get("subject_id")
        if subject is None:
            return None
        try:
            if kind == "ticket_changed":
                return self.store.get_ticket(project_id, subject)
            if kind == "agent_changed":
                return serialize_agent(
                    self.store, self.store.get_agent(subject, project_id))
            if kind == "review_changed":
                return self.store.get_review(project_id, subject)
            if kind == "assignment_changed":
                row = self.store.conn.execute(
                    "SELECT * FROM assignments WHERE id = ?", (subject,)
                ).fetchone()
                return serialize_assignment(row)
            if kind == "master_lease_changed":
                row = self.store.conn.execute(
                    "SELECT * FROM master_lease WHERE project_id = ?", (project_id,)
                ).fetchone()
                return serialize_master_lease(row)
        except Exception:
            return None
        return None

    def visible_to(self, principal, envelope, audit_row):
        """ACL filter, applied before a frame is written to the socket.

        Two rules, in order of cost. Project scope is the floor and rejects
        almost everything that should not be here. The per-channel rule -- a
        subscriber never receives events for a channel it cannot read -- is the
        T-187 half, and it has to be applied *here*: filtering `GET /messages`
        alone would leave the stream as a side channel that announces private
        traffic to everyone with a credential for the project.

        The check is deliberately fail-closed. A message event whose channel
        cannot be resolved, or whose subject has gone, is dropped rather than
        emitted, because the failure mode of guessing wrong in the other
        direction is a private conversation on somebody else's socket.
        """
        if audit_row["project_id"] != principal.project_id:
            return False
        channel_id = self._channel_of_event(audit_row)
        if channel_id is None:
            return True
        return self._may_read_channel(principal, audit_row["project_id"],
                                      channel_id)

    def _channel_of_event(self, audit_row):
        """The channel an event belongs to, or None if it is not channel-bound.

        Only message subjects carry one. Ticket and agent events are
        project-scoped and stay visible to every credential in the project.
        """
        if audit_row["subject_type"] != "message" or not audit_row["subject_id"]:
            return None
        row = self.store.conn.execute(
            "SELECT channel_id FROM messages WHERE id = ?",
            (audit_row["subject_id"],),
        ).fetchone()
        if row is None:
            # The subject is gone or is not a message we can resolve. Fail
            # closed: a sentinel the caller reads as "not visible".
            return _UNRESOLVED_CHANNEL
        return row["channel_id"]

    def _may_read_channel(self, principal, project_id, channel_id):
        if channel_id is _UNRESOLVED_CHANNEL:
            return False
        channel = self.store.conn.execute(
            "SELECT visibility FROM channels WHERE id = ? AND project_id = ?",
            (channel_id, project_id),
        ).fetchone()
        if channel is None:
            return False
        if channel["visibility"] == "public":
            return True
        member_id = self._member_id_of_principal(principal, project_id)
        if member_id is None:
            return False
        return self.store.conn.execute(
            "SELECT 1 FROM channel_members WHERE project_id = ? AND channel_id = ?"
            " AND member_id = ?", (project_id, channel_id, member_id),
        ).fetchone() is not None

    def _member_id_of_principal(self, principal, project_id):
        """The `mem_` id behind a credential, or None.

        Duplicated in shape from `messaging._member_id_of` but not shared: that
        one raises on a revoked membership because a *request* must be told;
        this one only ever returns a value, because a stream filter that raises
        would tear down the socket instead of dropping one frame.
        """
        if principal is None:
            return None
        if getattr(principal, "is_agent", False):
            row = self.store.conn.execute(
                "SELECT id FROM members WHERE project_id = ? AND agent_id = ?"
                " AND state = 'active'", (project_id, principal.agent_id),
            ).fetchone()
        else:
            row = self.store.conn.execute(
                "SELECT id FROM members WHERE project_id = ? AND id = ?"
                " AND state = 'active'", (project_id, principal.operator_id),
            ).fetchone()
        return row["id"] if row is not None else None

    # ---------------------------------------------------------------- stream

    def start_position(self, project_id, last_event_id):
        """Where to resume, and whether the client must re-read first.

        Returns `(after_seq, expired)`. `expired` is true when the requested id
        has fallen out of the retention window, which is the only case that
        emits `snapshot_required`.
        """
        head = head_event(self.store, project_id)["seq"]
        if not last_event_id:
            return head, False
        requested = seq_of(last_event_id)
        if requested is None:
            # An unparseable id is treated as expired rather than as "start from
            # zero": replaying the whole board to a client that sent garbage is
            # the more surprising of the two failures.
            return head, True
        if requested < head - self.retention:
            return head, True
        return requested, False

    def frames(self, project_id, principal, *, last_event_id=None,
               stop=None, max_frames=None, idle_deadline=None):
        """Yield encoded SSE frames until `stop()` or the deadline.

        `stop`, `max_frames` and `idle_deadline` exist so tests can drive the
        generator deterministically; a live connection passes only `stop`.
        """
        stop = stop or (lambda: False)
        after_seq, expired = self.start_position(project_id, last_event_id)
        emitted = 0
        yield b"retry: 2000\n\n"

        if expired:
            head = head_event(self.store, project_id)["seq"]
            frame = self.control("snapshot_required", head, reason="cursor_expired")
            # No id: on a control frame either. A client that stored it as its
            # cursor would resume from a position the trail never contained.
            yield encode_frame(frame, with_id=False)
            emitted += 1

        last_activity = time.monotonic()
        while not stop():
            rows = self.store.audit_trail(project_id, after_seq=after_seq, limit=200)
            head = head_event(self.store, project_id)["seq"]
            for row in rows:
                seq = seq_of(row["event_id"])
                after_seq = max(after_seq, seq if seq is not None else after_seq)
                envelope = self.envelope(row, head, principal)
                if not self.visible_to(principal, envelope, row):
                    continue
                yield encode_frame(envelope)
                emitted += 1
                last_activity = time.monotonic()
                if max_frames is not None and emitted >= max_frames:
                    return
            if rows:
                continue
            if time.monotonic() - last_activity >= self.heartbeat_seconds:
                yield encode_frame(self.control("heartbeat", head), with_id=False)
                emitted += 1
                last_activity = time.monotonic()
                if max_frames is not None and emitted >= max_frames:
                    return
            if idle_deadline is not None and time.monotonic() >= idle_deadline:
                return
            time.sleep(self.poll_seconds)
