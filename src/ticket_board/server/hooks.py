"""Hook event intake: dedupe, bounded context, and the four health booleans.

The rules this route exists to keep, all from the freeze:

* **Deduplicate on `event_id`.** A retried event produces exactly one audit
  record and still gets a response, because the adapter retries on a timeout it
  cannot distinguish from a failure.
* **A probe never sets `session_adopted`.** The synthetic connection test proves
  the server is reachable, not that a real Claude session adopted the hook.
  Conflating them is how a doctor shows green for a connection that will never
  deliver an event.
* **`stop` means "Turn finished".** It is not ticket completion and changes no
  ticket state here.
* **Bounded metadata only.** The stored columns are exactly the contract's
  fields; `note` is the single free-text one, and it exists for explicit user
  notes.
* **This route never grants a claim.** Adapter health and work authorization
  are separate; the claim path checks the session lease itself.
"""

from __future__ import annotations

import json
import time

from ..storage import ids
from ..storage.db import write_txn
from .auth import SESSION_LEASE_SECONDS, in_seconds
from .errors import MalformedRequest, NotFound, RateLimited

MAX_CONTEXT_LINES = 20          # the contract's maxItems
CONTEXT_LINE_LENGTH = 300

# Rate limit. The contract specifies that 429 exists and leaves the numbers
# open; these are generous next to a 30-second heartbeat and tight enough that
# a hot loop is stopped rather than allowed to fill the trail.
RATE_LIMIT_EVENTS = 120
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_RETRY_AFTER = 5

KINDS = ("session_start", "user_prompt_submit", "stop", "probe")
_EVENT_FIELDS = {"event_id", "agent_id", "session_id", "kind", "occurred_at",
                 "cwd", "note"}


class RateLimiter:
    """A fixed-window counter per agent, in memory.

    In memory on purpose: the limit protects this process's write path, it is
    not an account-level quota, and persisting it would mean a database write
    per hook event to police hook events.
    """

    def __init__(self, limit=RATE_LIMIT_EVENTS, window=RATE_LIMIT_WINDOW_SECONDS):
        self.limit = limit
        self.window = window
        self._hits = {}

    def check(self, key, *, now=None):
        now = time.monotonic() if now is None else now
        start, count = self._hits.get(key, (now, 0))
        if now - start >= self.window:
            start, count = now, 0
        count += 1
        self._hits[key] = (start, count)
        if count > self.limit:
            raise RateLimited(RATE_LIMIT_RETRY_AFTER)


def validate_event(event):
    if not isinstance(event, dict):
        raise MalformedRequest("event must be an object.",
                               {"rejected_fields": ["event"]})
    unexpected = sorted(set(event) - _EVENT_FIELDS)
    if unexpected:
        # An adapter that starts sending prompts or tool arguments in a new
        # field is refused here rather than quietly stored.
        raise MalformedRequest("event contained unexpected fields.",
                               {"rejected_fields": unexpected})
    for field in ("event_id", "agent_id", "session_id", "kind", "occurred_at"):
        if not isinstance(event.get(field), str) or not event[field]:
            raise MalformedRequest("event.{} is required.".format(field),
                                   {"missing_fields": ["event." + field]})
    if event["kind"] not in KINDS:
        raise MalformedRequest("event.kind is not a known hook kind.",
                               {"rejected_fields": ["event.kind"]})
    if not event["event_id"].startswith("hev_") or len(event["event_id"]) > 68:
        raise MalformedRequest("event.event_id must look like hev_...",
                               {"rejected_fields": ["event.event_id"]})
    for field in ("cwd", "note"):
        value = event.get(field)
        if value is not None and not isinstance(value, str):
            raise MalformedRequest("event.{} must be a string or null.".format(field),
                                   {"rejected_fields": ["event." + field]})
    if isinstance(event.get("note"), str) and len(event["note"]) > 2000:
        raise MalformedRequest("event.note is too long.",
                               {"rejected_fields": ["event.note"]})
    return event


def record(store, project_id, principal, event, *, request_id=None,
           lease_seconds=SESSION_LEASE_SECONDS):
    """Store one hook event. Returns `(deduplicated, agent)`."""
    with write_txn(store.conn) as conn:
        agent = conn.execute(
            "SELECT * FROM agents WHERE id = ? AND project_id = ?",
            (event["agent_id"], project_id),
        ).fetchone()
        if agent is None:
            raise NotFound("No such agent in this project.",
                           {"agent_id": event["agent_id"]})

        existing = conn.execute(
            "SELECT 1 FROM hook_events WHERE event_id = ?", (event["event_id"],)
        ).fetchone()
        if existing is not None:
            # Exactly one audit record for a retried event: return without
            # writing anything at all, health included. The response is still
            # produced by the caller.
            return True, store.get_agent(event["agent_id"])

        conn.execute(
            "INSERT INTO hook_events (event_id, project_id, agent_id, session_id,"
            " kind, occurred_at, cwd, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event["event_id"], project_id, event["agent_id"], event["session_id"],
             event["kind"], event["occurred_at"], event.get("cwd"),
             event.get("note")),
        )

        health = json.loads(agent["hook_health"] or "{}")
        health["server_received"] = True
        # The server did produce and return a context payload, which is what
        # this boolean means. It deliberately does not mean the session rendered
        # it -- that is what session_adopted is for, and only a real event sets
        # that one.
        health["response_delivered"] = True
        if event["kind"] != "probe":
            health["session_adopted"] = True
        health.setdefault("config_installed", False)
        health.setdefault("last_error", None)

        now = ids.now()
        conn.execute(
            "UPDATE agents SET hook_health = ?, last_heartbeat_at = ?,"
            " version = version + 1 WHERE id = ?",
            (json.dumps(health), now, event["agent_id"]),
        )
        # An active session keeps its lease. Silence lets it lapse, which stops
        # new claims -- and lapsing is not revocation: no note is written and
        # nothing declares the agent dead.
        conn.execute(
            "UPDATE session_leases SET expires_at = ?, version = version + 1"
            " WHERE session_id = ? AND revoked_at IS NULL",
            (in_seconds(lease_seconds), event["session_id"]),
        )
        store._audit(conn, project_id, principal.actor,
                     "hook.{}".format(event["kind"]),
                     subject_type="agent", subject_id=event["agent_id"],
                     request_id=request_id,
                     summary="hook {} from {}".format(event["kind"],
                                                      event["session_id"]))
    return False, store.get_agent(event["agent_id"])


def context_for(store, project_id, agent, event):
    """Bounded context injected back into the session.

    Never unbounded board state: the adapter renders these as text at
    SessionStart/UserPromptSubmit, and a long dump would push the session's own
    work out of the window it was trying to protect.
    """
    lines = []
    current = agent.get("current_ticket")
    if event["kind"] == "probe":
        lines.append("Connection test received. "
                     "This does not prove session adoption.")
    if current:
        try:
            ticket = store.get_ticket(project_id, current)
        except NotFound:
            ticket = None
        if ticket is not None:
            claimed = (ticket.get("claimed_at") or "")[11:16]
            lines.append("You hold {}{}.".format(
                current, " (claimed {})".format(claimed) if claimed else ""))
            if ticket.get("next_step"):
                lines.append("Next step you recorded: {}".format(ticket["next_step"]))
            if ticket.get("dependency_blocked"):
                lines.append("Waiting on {}.".format(
                    ", ".join(ticket.get("dependencies", []))))
    elif event["kind"] != "probe":
        lines.append("You hold no ticket. Claim one before starting work.")
    if event["kind"] == "stop":
        # The one piece of copy that is contract, not styling.
        lines.append("Turn finished. This does not complete a ticket.")

    return {
        "lines": [line[:CONTEXT_LINE_LENGTH] for line in lines[:MAX_CONTEXT_LINES]],
        "current_ticket": current,
        # Messaging is T-187. Reporting 0 rather than omitting the field keeps
        # the shape stable for the console; it is not a claim that the board has
        # no messages, it is that this build has no message records at all.
        "unread_messages": 0,
    }
