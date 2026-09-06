"""Response assembly: serializers, screen envelopes, cursors.

Everything that turns stored records into the shapes `openapi.yaml` publishes
lives here, so the route handlers stay about authorization and ordering.

Two rules from the T-179 handoff are load-bearing and easy to break by
"tidying" a serializer:

* **Null versus omitted.** Optional fields declared `anyOf [..., null]` are sent
  as `null`; optional fields declared with a plain type are *omitted*. The store
  already gets this right for records it owns -- so records pass through
  untouched, and only fields this layer adds are built by hand.
* **`dependency_blocked` is derived**, never a state and never a filter column.
"""

from __future__ import annotations

import base64
import datetime as _dt
import json

from ..storage import ids

# Liveness policy. The Agent schema states the pilot defaults; they are policy
# rather than contract, so they are named constants a deployment can move.
HEARTBEAT_STALE_SECONDS = 90
HEARTBEAT_OFFLINE_SECONDS = 300
PROGRESS_STALLED_SECONDS = 900

# How far back the SSE stream will replay. Beyond this a resuming client is
# told `snapshot_required` / `cursor_expired` rather than silently skipped.
# The contract requires a documented window, not a particular length.
SSE_RETENTION_EVENTS = 1000

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200

# Server-supplied screen copy. It lives on the server so every client renders
# the same words; the console is told to render `empty_state.headline` rather
# than hardcode its own.
EMPTY_STATES = {
    "overview": {
        "headline": "No work yet. Create a ticket or import a board.",
        "detail": None,
        "primary_action": "New ticket",
    },
    "tickets": {
        "headline": "No tickets match this filter.",
        "detail": "Clear the filter or create a ticket.",
        "primary_action": "New ticket",
    },
    "agents": {
        "headline": "Connect your first agent.",
        "detail": None,
        "primary_action": "Connect agent",
    },
    "activity": {
        "headline": "No activity yet.",
        "detail": None,
        "primary_action": None,
    },
}


def _parse(stamp):
    return _dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=_dt.timezone.utc
    )


def age_seconds(stamp, *, now=None):
    """Seconds between `stamp` and now. `None` reads as infinitely old."""
    if not stamp:
        return None
    reference = _parse(now) if now else _dt.datetime.now(_dt.timezone.utc)
    return (reference - _parse(stamp)).total_seconds()


# ------------------------------------------------------------------ cursors

def encode_cursor(kind, value):
    """Opaque by contract; the encoding is ours to pick and to change.

    Base64 of a typed pair, so a cursor from one listing cannot be replayed
    against another and quietly return a wrong page.
    """
    raw = json.dumps({"k": kind, "v": value}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(kind, cursor):
    from .errors import MalformedRequest

    if cursor is None:
        return None
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        parsed = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if parsed.get("k") != kind:
            raise ValueError("cursor is for a different listing")
        return parsed["v"]
    except Exception:
        raise MalformedRequest("Cursor is not valid for this listing.",
                               {"rejected_fields": ["cursor"]})


def page_limit(raw):
    from .errors import MalformedRequest

    if raw is None:
        return DEFAULT_PAGE_LIMIT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise MalformedRequest("limit must be an integer.",
                               {"rejected_fields": ["limit"]})
    if value < 1 or value > MAX_PAGE_LIMIT:
        raise MalformedRequest(
            "limit must be between 1 and {}.".format(MAX_PAGE_LIMIT),
            {"rejected_fields": ["limit"]},
        )
    return value


# ------------------------------------------------------------ stream status

def head_event(store, project_id):
    """The newest audit row for a project: the snapshot version and cursor."""
    row = store.conn.execute(
        "SELECT seq, event_id, occurred_at FROM audit_events"
        " WHERE project_id = ? ORDER BY seq DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if row is None:
        return {"seq": 0, "event_id": None, "occurred_at": None}
    return {"seq": row["seq"], "event_id": row["event_id"],
            "occurred_at": row["occurred_at"]}


def stream_status(store, project_id, *, state="live"):
    """The `StreamStatus` every screen carries.

    `as_of` is the server's read time, not the last event time: the banner the
    console renders is "showing updates from <as_of>", and dating it from the
    last event would claim freshness the snapshot does not have on a quiet
    board.
    """
    head = head_event(store, project_id)
    return {
        "state": state,
        "snapshot_version": head["seq"],
        "as_of": ids.now(),
        "last_event_id": head["event_id"],
    }


# ------------------------------------------------------------- serializers

def serialize_agent(store, agent):
    """`Agent`, with the two things the store does not join in.

    The store owns agent columns; the session lease is a separate record and
    `Agent.session` is what the console renders as "connected as ses_...", so
    the join happens here rather than in every caller.
    """
    payload = dict(agent)
    lease = latest_session(store, agent["id"])
    payload["session"] = lease
    health = dict(payload.get("hook_health") or {})
    health.setdefault("last_error", None)
    payload["hook_health"] = health
    payload["state"] = derive_agent_state(store, agent, lease)
    return payload


def latest_session(store, agent_id):
    row = store.conn.execute(
        "SELECT * FROM session_leases WHERE agent_id = ?"
        " ORDER BY acquired_at DESC, rowid DESC LIMIT 1",
        (agent_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "session_id": row["session_id"],
        "agent_id": row["agent_id"],
        "acquired_at": row["acquired_at"],
        "expires_at": row["expires_at"],
        "version": row["version"],
        "revoked_at": row["revoked_at"],
        "revocation_note": row["revocation_note"],
    }


def session_is_live(lease, *, now=None):
    """Unrevoked and unexpired. Expiry and revocation are different things.

    Revocation is an explicit act with a note; expiry is the passage of time.
    The contract refuses to infer death from silence, so an expired lease does
    not mark an agent revoked -- it only stops that session claiming new work.
    """
    if lease is None or lease["revoked_at"] is not None:
        return False
    return lease["expires_at"] > (now or ids.now())


def derive_agent_state(store, agent, lease):
    """`AgentState`, computed at read time.

    The stored column is set once at enrollment and nothing keeps it current,
    so a derived value is the honest one: liveness is a function of the clock,
    and a cached "working" that outlives its session is exactly the false green
    the design doc warns about. The order below is a priority list, not a
    sequence of independent tests.
    """
    if lease is not None and lease["revoked_at"] is not None:
        return "revoked"
    heartbeat_age = age_seconds(agent.get("last_heartbeat_at"))
    if (agent.get("hook_health") or {}).get("last_error"):
        return "offline"
    if heartbeat_age is not None and heartbeat_age > HEARTBEAT_OFFLINE_SECONDS:
        return "offline"
    if agent.get("capacity", {}).get("active_tickets"):
        return "working"
    if session_is_live(lease):
        return "idle"
    if (agent.get("hook_health") or {}).get("server_received"):
        return "connected"
    return "idle"


def serialize_assignment(row):
    """`Assignment` from an assignments row.

    The store returns the raw row, which carries `project_id`; the contract's
    Assignment is `additionalProperties: false` and has no such field, so the
    row cannot be returned as-is. Reshaped here, and asserted by the
    conformance test rather than by reading.
    """
    if row is None:
        return None
    return {
        "id": row["id"],
        "ticket_id": row["ticket_id"],
        "agent_id": row["agent_id"],
        "state": row["state"],
        "reason": row["reason"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "lease_epoch": row["lease_epoch"],
        "version": row["version"],
    }


def serialize_master_lease(row, *, project_id=None):
    """`MasterLease`, including the never-held case.

    An unheld board still has to answer `GET /master`: the panel renders "No
    master" rather than an error, and `holder: null` at epoch 0 is what says so.
    """
    if row is None:
        now = ids.now()
        return {
            "holder": None,
            "epoch": 0,
            "acquired_at": now,
            "expires_at": now,
            "routing_mode": "deterministic",
            "paused": False,
            "last_sweep_at": None,
            "next_sweep_at": None,
            "sweep_interval_seconds": 30,
        }
    holder = json.loads(row["holder"]) if row["holder"] else None
    # An expired lease has no holder. Fencing is by epoch, but the panel must
    # not show a stale name as if someone were still routing.
    if holder is not None and row["expires_at"] <= ids.now():
        holder = None
    return {
        "holder": holder,
        "epoch": row["epoch"],
        "acquired_at": row["acquired_at"],
        "expires_at": row["expires_at"],
        "routing_mode": row["routing_mode"],
        "paused": bool(row["paused"]),
        "last_sweep_at": row["last_sweep_at"],
        "next_sweep_at": row["next_sweep_at"],
        "sweep_interval_seconds": row["sweep_interval_seconds"],
    }


# ------------------------------------------------------------------ screens

def overview_counts(tickets):
    counts = {"ready": 0, "in_progress": 0, "awaiting_review": 0, "blocked": 0,
              "dependency_blocked": 0}
    for ticket in tickets:
        state = ticket["state"]
        if state == "blocked":
            counts["blocked"] += 1
        elif state == "claimed":
            counts["in_progress"] += 1
        elif state == "review":
            counts["awaiting_review"] += 1
        elif state == "open":
            # Ready means claimable now. A dependency-blocked ticket is open but
            # not ready, and it is counted separately rather than twice.
            if ticket["dependency_blocked"]:
                counts["dependency_blocked"] += 1
            else:
                counts["ready"] += 1
    return counts


def attention_items(store, project_id, tickets, agents, lease):
    """The Overview attention queue, newest signal per subject.

    Every entry names a subject the operator can act on. `acknowledged_by` is
    always present and `null` for now -- acknowledgement is an operator action
    with no route in the frozen contract, so the field is carried rather than
    invented.
    """
    now = ids.now()
    items = []
    for ticket in tickets:
        if ticket["state"] == "review":
            items.append(_attention("awaiting_review", "ticket", ticket["id"],
                                    "{} is awaiting review.".format(ticket["id"]),
                                    ticket["updated_at"]))
        elif ticket["state"] == "blocked":
            reason = ticket.get("blocked_reason") or "no reason recorded"
            items.append(_attention("blocked", "ticket", ticket["id"],
                                    "{} is blocked: {}".format(ticket["id"], reason)[:200],
                                    ticket["updated_at"]))
    for agent in agents:
        health = agent.get("hook_health") or {}
        if health.get("last_error"):
            items.append(_attention(
                "hook_delivery_failed", "agent", agent["id"],
                "Hook delivery failed: {}".format(health["last_error"])[:200],
                agent.get("last_heartbeat_at") or agent["created_at"]))
        heartbeat_age = age_seconds(agent.get("last_heartbeat_at"), now=now)
        if heartbeat_age is not None and heartbeat_age > HEARTBEAT_OFFLINE_SECONDS:
            items.append(_attention(
                "no_heartbeat", "agent", agent["id"],
                "No heartbeat for {}m. Check session.".format(int(heartbeat_age // 60)),
                agent["last_heartbeat_at"]))
        elif heartbeat_age is not None and heartbeat_age <= HEARTBEAT_STALE_SECONDS:
            # The signal this pair of fields exists for: alive, but not moving.
            # Collapsing heartbeat and progress would hide exactly this.
            progress_age = age_seconds(agent.get("last_progress_at"), now=now)
            if agent.get("capacity", {}).get("active_tickets") and (
                    progress_age is None or progress_age > PROGRESS_STALLED_SECONDS):
                since = (agent.get("last_progress_at") or "")[11:16] or "start"
                items.append(_attention(
                    "progress_stalled", "agent", agent["id"],
                    "Heartbeat is live but no progress since {}.".format(since),
                    agent.get("last_progress_at") or agent["created_at"]))
    # Note what this cannot key on: `holder`. `serialize_master_lease` nulls the
    # holder once the lease lapses, so "holder set and expired" is a state that
    # never occurs and an alert written that way would never fire. The signal is
    # that a master once existed (epoch > 0) and nobody is routing now.
    if lease["epoch"] > 0 and lease["expires_at"] <= now:
        items.append(_attention("master_lease_expired", "master_lease",
                                str(lease["epoch"]),
                                "Master lease expired at epoch {}. "
                                "No one is routing work.".format(lease["epoch"]),
                                lease["expires_at"]))
    items.sort(key=lambda item: (item["raised_at"], item["subject_id"]), reverse=True)
    return items


def _attention(kind, subject_type, subject_id, headline, raised_at):
    return {
        "kind": kind,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "headline": headline[:200],
        "raised_at": raised_at or ids.now(),
        "acknowledged_by": None,
    }


def available_actions(ticket, principal, *, pending_review=None, is_master=False):
    """Role-filtered actions for this principal on this ticket.

    Advisory only: every route re-checks authorization regardless of what the
    UI was told. The list exists so the console does not render a button the
    server will refuse, not as the authorization itself.
    """
    actions = []
    state = ticket["state"]
    owns = principal.is_agent and ticket.get("owner") == principal.agent_id

    if principal.is_operator or is_master:
        if state == "open" and not ticket["dependency_blocked"]:
            actions.append("assign")
    if principal.is_agent and state == "open" and not ticket["dependency_blocked"]:
        actions.append("claim")
    if owns and state in ("claimed", "review"):
        actions.append("update")
    if owns and state == "claimed":
        actions.append("request_review")
    if principal.is_operator and state == "review" and pending_review is not None:
        # The reviewer must differ from the submitter; offering "accept" to the
        # submitter and refusing it afterwards is a worse experience than not
        # offering it, and the server refuses either way.
        submitter = (pending_review.get("submitted_by") or {}).get("id")
        if submitter != principal.id:
            actions.extend(["accept", "reject"])
    if state == "blocked":
        if principal.is_operator or owns:
            actions.append("unblock")
    elif state in ("open", "claimed", "review"):
        if principal.is_operator or owns:
            actions.append("block")
    return actions
