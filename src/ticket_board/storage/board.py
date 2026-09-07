"""Transactional board state.

Public surface for T-180 (routes) and T-182 (master loop). Everything that
mutates goes through a `BEGIN IMMEDIATE` transaction, writes an audit row, and
returns contract-shaped dicts.

The invariants worth stating once, because they are the ones that break
quietly:

- A ticket's `version` is bumped on every mutation, and every mutation takes an
  `expected_version`. Mismatch is a 409 carrying both versions.
- `dependency_blocked` is computed on read, never stored.
- Claiming is atomic across processes: state check, capacity check and write
  happen inside one immediate transaction.
- A late update from a displaced session is stored with `superseded=1` and has
  no effect on ticket state. An update with no session_id at all is likewise
  denied effect on ticket state (T-239) -- absent is not "clean", it is a
  third, unattributed case, audited as such.
"""

import hashlib
import json
import threading

from . import ids
from .db import connect, read_txn, write_txn
from .errors import (
    CapacityExhausted,
    DependencyCycle,
    DependencyUnmet,
    InvalidReviewEvidence,
    InvalidStateTransition,
    MalformedRequest,
    MissingAcceptanceCriteria,
    NotFound,
    RequestIdReused,
    SessionLeaseExpired,
    TicketAlreadyClaimed,
    TicketVersionConflict,
)
from .messaging import MessagingMixin

ACTIVE_STATES = ("claimed", "review")
TERMINAL_STATES = ("done",)

# Transitions the store will perform. Anything absent is 422.
ALLOWED_TRANSITIONS = {
    "open": {"claimed", "blocked", "done"},
    "claimed": {"review", "open", "blocked", "done"},
    "review": {"done", "claimed", "open", "blocked"},
    "blocked": {"open", "claimed"},
    "done": set(),
}

SYSTEM_ACTOR = {"type": "system", "id": "system", "display_name": "server"}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _body_hash(operation, body):
    return hashlib.sha256(
        (operation + "\n" + _json(body)).encode("utf-8")
    ).hexdigest()


def _omit_none(payload, fields):
    """Drop optional fields that are absent.

    The contract distinguishes two kinds of "no value": a field declared
    `anyOf [..., null]` (send null -- `owner`, `blocked_reason`) and an
    optional field with a plain type (omit it entirely -- `outcome`, `role`,
    `notes`). Sending null for the second kind fails schema validation under
    `additionalProperties: false`, so absent values are omitted here rather
    than at every call site. T-180 must preserve this when it serializes
    responses.
    """
    for field in fields:
        if payload.get(field) is None:
            payload.pop(field, None)
    return payload


class BoardStore(MessagingMixin):
    """Transactional store for one board database (may hold many projects)."""

    SYSTEM_ACTOR = SYSTEM_ACTOR

    def __init__(self, path):
        self.path = str(path)
        self._local = threading.local()
        self._all_conns = []
        self._conns_lock = threading.Lock()
        # Open eagerly so a bad path fails at construction, not at first use.
        self._connect()

    def _connect(self):
        conn = connect(self.path)
        self._local.conn = conn
        with self._conns_lock:
            self._all_conns.append(conn)
        return conn

    @property
    def conn(self):
        """This thread's connection.

        SQLite connections are thread-bound, and T-180 serves from a thread
        pool. Handing every thread its own connection keeps the concurrency
        story identical whether callers are threads or processes: correctness
        rests on `BEGIN IMMEDIATE` in the database, not on a lock in this
        object. A single shared connection with a mutex would serialize the
        dashboard's reads behind the claim path for no benefit.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
        return conn

    def close(self):
        """Close every connection this store opened, from any thread."""
        with self._conns_lock:
            conns, self._all_conns = self._all_conns, []
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
        self._local.conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------------------------------------------------------- audit

    def _next_seq(self, conn):
        row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM audit_events").fetchone()
        return int(row[0])

    def _audit(self, conn, project_id, actor, action, *, subject_type=None,
               subject_id=None, request_id=None, summary=""):
        seq = self._next_seq(conn)
        row = {
            "id": ids.audit_id(),
            "seq": seq,
            "event_id": ids.event_id(seq),
            "project_id": project_id,
            "actor": _json(actor),
            "action": action,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "request_id": request_id,
            "occurred_at": ids.now(),
            "summary": summary,
        }
        conn.execute(
            "INSERT INTO audit_events (id, seq, event_id, project_id, actor, action,"
            " subject_type, subject_id, request_id, occurred_at, summary)"
            " VALUES (:id, :seq, :event_id, :project_id, :actor, :action,"
            " :subject_type, :subject_id, :request_id, :occurred_at, :summary)",
            row,
        )
        return row["event_id"]

    def audit_trail(self, project_id, *, after_seq=0, limit=200):
        rows = self.conn.execute(
            "SELECT * FROM audit_events WHERE project_id = ? AND seq > ?"
            " ORDER BY seq LIMIT ?",
            (project_id, after_seq, limit),
        ).fetchall()
        return [
            _omit_none({
                "id": r["id"],
                "event_id": r["event_id"],
                "project_id": r["project_id"],
                "actor": json.loads(r["actor"]),
                "action": r["action"],
                "subject_type": r["subject_type"],
                "subject_id": r["subject_id"],
                "request_id": r["request_id"],
                "occurred_at": r["occurred_at"],
                "summary": r["summary"] or "",
            }, ("subject_type", "subject_id"))
            for r in rows
        ]

    # --------------------------------------------------------- idempotency

    def _replay(self, conn, project_id, request_id, operation, body):
        """Return a stored response for this request_id, or None.

        Raises RequestIdReused when the id was used with a different body --
        the contract's rule, and the reason this is checked before any write.
        """
        if request_id is None:
            return None
        if not ids.REQUEST_ID_RE.match(request_id):
            raise MalformedRequest(
                "request_id must be a UUID.", {"request_id": request_id}
            )
        row = conn.execute(
            "SELECT body_hash, response FROM request_log"
            " WHERE project_id = ? AND request_id = ?",
            (project_id, request_id),
        ).fetchone()
        if row is None:
            return None
        if row["body_hash"] != _body_hash(operation, body):
            raise RequestIdReused(request_id)
        return json.loads(row["response"])

    def _remember(self, conn, project_id, request_id, operation, body, response):
        if request_id is None:
            return
        conn.execute(
            "INSERT INTO request_log (request_id, project_id, operation, body_hash,"
            " response, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                request_id,
                project_id,
                operation,
                _body_hash(operation, body),
                _json(response),
                ids.now(),
            ),
        )

    # -------------------------------------------------------------- project

    def create_project(self, name, *, source="native", project_id=None):
        pid = project_id or ids.project_id()
        with write_txn(self.conn) as conn:
            conn.execute(
                "INSERT INTO projects (id, name, version, created_at, source, paused)"
                " VALUES (?, ?, 1, ?, ?, 0)",
                (pid, name, ids.now(), source),
            )
            self._audit(conn, pid, SYSTEM_ACTOR, "project.create",
                        subject_type="project", subject_id=pid,
                        summary="created project {}".format(name))
        return self.get_project(pid)

    def get_project(self, project_id):
        row = self.conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            raise NotFound("No such project.", {"project_id": project_id})
        return {
            "id": row["id"],
            "name": row["name"],
            "version": row["version"],
            "created_at": row["created_at"],
            "source": row["source"],
            "paused": bool(row["paused"]),
        }

    # ---------------------------------------------------------------- agent

    def create_agent(self, project_id, name, *, role=None, capabilities=None,
                     connection_mode="hook_only", max_active_tickets=1,
                     runtime=None, agent_id=None):
        aid = agent_id or ids.agent_id()
        with write_txn(self.conn) as conn:
            conn.execute(
                "INSERT INTO agents (id, project_id, name, role, capabilities, state,"
                " connection_mode, runtime, max_active_tickets, hook_health, version,"
                " created_at) VALUES (?, ?, ?, ?, ?, 'idle', ?, ?, ?, ?, 1, ?)",
                (
                    aid, project_id, name, role, _json(capabilities or []),
                    connection_mode, _json(runtime or {}), max_active_tickets,
                    _json({
                        "config_installed": False,
                        "server_received": False,
                        "response_delivered": False,
                        "session_adopted": False,
                    }),
                    ids.now(),
                ),
            )
            self._audit(conn, project_id, SYSTEM_ACTOR, "agent.create",
                        subject_type="agent", subject_id=aid,
                        summary="registered agent {}".format(name))
        return self.get_agent(aid)

    def get_agent(self, agent_id, project_id=None):
        """`project_id` is optional so trusted internal callers -- callers
        that already minted or verified `agent_id` within the project they
        expect it to belong to -- can keep calling this unscoped. Any caller
        taking `agent_id` from outside that trust boundary (an API route
        comparing it against the caller's own ctx.project_id) must pass
        `project_id`: wrong-project and nonexistent then raise the identical
        NotFound, so neither is distinguishable from the other (T-240 -- see
        `__review` above for the matching reasoning on reviews).
        """
        row = self.conn.execute(
            "SELECT * FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        if row is None or (project_id is not None and row["project_id"] != project_id):
            raise NotFound("No such agent.", {"agent_id": agent_id})
        return self._agent_row(row)

    def _agent_row(self, row):
        active = self.conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE owner = ? AND state IN (?, ?)",
            (row["id"],) + ACTIVE_STATES,
        ).fetchone()[0]
        payload = {
            "id": row["id"],
            "project_id": row["project_id"],
            "name": row["name"],
            "role": row["role"],
            "capabilities": json.loads(row["capabilities"]),
            "state": row["state"],
            "connection_mode": row["connection_mode"],
            "runtime": json.loads(row["runtime"]),
            "capacity": {
                "max_active_tickets": row["max_active_tickets"],
                "active_tickets": active,
            },
            "current_ticket": row["current_ticket"],
            "worktree": row["worktree"],
            "hook_health": json.loads(row["hook_health"]),
            # Two fields, two meanings. A live heartbeat with stale progress is
            # exactly the signal the Overview attention queue renders.
            "last_heartbeat_at": row["last_heartbeat_at"],
            "last_progress_at": row["last_progress_at"],
            "version": row["version"],
            "created_at": row["created_at"],
        }
        return _omit_none(payload, ("role",))

    def record_heartbeat(self, agent_id, *, at=None):
        """Liveness only. Deliberately does NOT touch last_progress_at."""
        with write_txn(self.conn) as conn:
            cur = conn.execute(
                "UPDATE agents SET last_heartbeat_at = ?, version = version + 1"
                " WHERE id = ?",
                (at or ids.now(), agent_id),
            )
            if cur.rowcount == 0:
                raise NotFound("No such agent.", {"agent_id": agent_id})
        return self.get_agent(agent_id)

    # --------------------------------------------------------- session lease

    def open_session(self, agent_id, expires_at, *, session_id=None, at=None):
        sid = session_id or ids.session_id()
        now = at or ids.now()
        with write_txn(self.conn) as conn:
            agent = conn.execute(
                "SELECT project_id FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
            if agent is None:
                raise NotFound("No such agent.", {"agent_id": agent_id})
            conn.execute(
                "INSERT INTO session_leases (session_id, agent_id, acquired_at,"
                " expires_at, version) VALUES (?, ?, ?, ?, 1)",
                (sid, agent_id, now, expires_at),
            )
            self._audit(conn, agent["project_id"], SYSTEM_ACTOR, "session.open",
                        subject_type="agent", subject_id=agent_id,
                        summary="session {} opened".format(sid))
        return self.get_session(sid)

    def get_session(self, session_id):
        row = self.conn.execute(
            "SELECT * FROM session_leases WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise NotFound("No such session.", {"session_id": session_id})
        return {
            "session_id": row["session_id"],
            "agent_id": row["agent_id"],
            "acquired_at": row["acquired_at"],
            "expires_at": row["expires_at"],
            "version": row["version"],
            "revoked_at": row["revoked_at"],
            "revocation_note": row["revocation_note"],
        }

    def revoke_session(self, session_id, *, note=None, at=None):
        with write_txn(self.conn) as conn:
            row = conn.execute(
                "SELECT agent_id FROM session_leases WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise NotFound("No such session.", {"session_id": session_id})
            conn.execute(
                "UPDATE session_leases SET revoked_at = ?, revocation_note = ?,"
                " version = version + 1 WHERE session_id = ?",
                (at or ids.now(), note, session_id),
            )
            agent = conn.execute(
                "SELECT project_id FROM agents WHERE id = ?", (row["agent_id"],)
            ).fetchone()
            self._audit(conn, agent["project_id"], SYSTEM_ACTOR, "session.revoke",
                        subject_type="agent", subject_id=row["agent_id"],
                        summary="session {} revoked".format(session_id))
        return self.get_session(session_id)

    def _session_is_current(self, conn, session_id):
        if session_id is None:
            return False
        row = conn.execute(
            "SELECT revoked_at FROM session_leases WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row is not None and row["revoked_at"] is None

    # --------------------------------------------------------------- tickets

    def _ticket_row(self, conn, project_id, ticket_id):
        row = conn.execute(
            "SELECT * FROM tickets WHERE project_id = ? AND id = ?",
            (project_id, ticket_id),
        ).fetchone()
        if row is None:
            raise NotFound("No such ticket.",
                           {"project_id": project_id, "ticket_id": ticket_id})
        return row

    def _dependencies(self, conn, project_id, ticket_id):
        return [
            r["depends_on"]
            for r in conn.execute(
                "SELECT depends_on FROM ticket_dependencies"
                " WHERE project_id = ? AND ticket_id = ? ORDER BY depends_on",
                (project_id, ticket_id),
            )
        ]

    def _unmet_dependencies(self, conn, project_id, deps):
        """Dependencies that are not done. Unknown ids count as unmet.

        An id that does not resolve is treated as unmet rather than ignored --
        silently satisfying a dependency on a ticket that does not exist is how
        a board lets work start before its predecessor.
        """
        unmet = []
        for dep in deps:
            row = conn.execute(
                "SELECT state FROM tickets WHERE project_id = ? AND id = ?",
                (project_id, dep),
            ).fetchone()
            if row is None or row["state"] != "done":
                unmet.append(dep)
        return unmet

    def _serialize_ticket(self, conn, row):
        deps = self._dependencies(conn, row["project_id"], row["id"])
        unmet = self._unmet_dependencies(conn, row["project_id"], deps)
        payload = {
            "id": row["id"],
            "project_id": row["project_id"],
            "title": row["title"],
            "outcome": row["outcome"],
            "acceptance": json.loads(row["acceptance"]),
            "state": row["state"],
            "version": row["version"],
            "role": row["role"],
            "owner": row["owner"],
            "owner_session": row["owner_session"],
            "dependencies": deps,
            # Derived, never stored -- T-178 freeze decision 2.
            "dependency_blocked": bool(unmet),
            "blocked_reason": row["blocked_reason"],
            "files": json.loads(row["files"]),
            "worktree": row["worktree"],
            "evidence": json.loads(row["evidence"]) if row["evidence"] else None,
            "next_step": row["next_step"],
            "handoff": row["handoff"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "claimed_at": row["claimed_at"],
            "last_progress_at": row["last_progress_at"],
        }
        return _omit_none(payload, ("outcome", "role"))

    def get_ticket(self, project_id, ticket_id):
        with read_txn(self.conn) as conn:
            return self._serialize_ticket(
                conn, self._ticket_row(conn, project_id, ticket_id)
            )

    def list_tickets(self, project_id, *, state=None):
        with read_txn(self.conn) as conn:
            if state:
                rows = conn.execute(
                    "SELECT * FROM tickets WHERE project_id = ? AND state = ?"
                    " ORDER BY id",
                    (project_id, state),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tickets WHERE project_id = ? ORDER BY id",
                    (project_id,),
                ).fetchall()
            return [self._serialize_ticket(conn, r) for r in rows]

    def _would_cycle(self, conn, project_id, ticket_id, new_deps):
        """Return the cycle path if ticket_id -> new_deps closes a loop.

        Walks the existing edges from each proposed dependency looking for a
        path back to ticket_id. Runs *before* any insert, because the contract
        requires a 422 cycle to create nothing at all.
        """
        for dep in new_deps:
            if dep == ticket_id:
                return [ticket_id, ticket_id]
            stack = [(dep, [ticket_id, dep])]
            seen = set()
            while stack:
                node, path = stack.pop()
                if node == ticket_id:
                    return path
                if node in seen:
                    continue
                seen.add(node)
                for r in conn.execute(
                    "SELECT depends_on FROM ticket_dependencies"
                    " WHERE project_id = ? AND ticket_id = ?",
                    (project_id, node),
                ):
                    stack.append((r["depends_on"], path + [r["depends_on"]]))
        return None

    def _set_dependencies(self, conn, project_id, ticket_id, deps):
        cycle = self._would_cycle(conn, project_id, ticket_id, deps)
        if cycle:
            raise DependencyCycle(cycle)
        conn.execute(
            "DELETE FROM ticket_dependencies WHERE project_id = ? AND ticket_id = ?",
            (project_id, ticket_id),
        )
        for dep in deps:
            conn.execute(
                "INSERT INTO ticket_dependencies (project_id, ticket_id, depends_on)"
                " VALUES (?, ?, ?)",
                (project_id, ticket_id, dep),
            )

    def create_ticket(self, project_id, ticket_id, title, *, role=None,
                      outcome=None, acceptance=None, dependencies=None,
                      files=None, actor=None, request_id=None):
        """`ticket_id` is either the id string, or a callable(conn) that mints
        one. The mint is resolved *after* the replay check so a retry under
        the same request_id never burns a second id, and the id -- which the
        caller never chose -- is never part of what the replay hash asserts
        was reused unchanged (see T-235: it used to be, which inverted the
        idempotency contract for server-minted ids).
        """
        actor = actor or SYSTEM_ACTOR
        body = {
            "title": title, "role": role,
            "outcome": outcome, "acceptance": acceptance or [],
            "dependencies": sorted(dependencies or []), "files": files or [],
        }
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id,
                                  "create_ticket", body)
            if replay is not None:
                return replay
            resolved_id = ticket_id(conn) if callable(ticket_id) else ticket_id
            if not ids.TICKET_ID_RE.match(resolved_id):
                raise MalformedRequest(
                    "ticket id must look like DEMO-13.", {"ticket_id": resolved_id}
                )
            now = ids.now()
            conn.execute(
                "INSERT INTO tickets (id, project_id, title, outcome, acceptance,"
                " state, version, role, files, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'open', 1, ?, ?, ?, ?)",
                (resolved_id, project_id, title, outcome, _json(acceptance or []),
                 role, _json(files or []), now, now),
            )
            self._set_dependencies(conn, project_id, resolved_id,
                                   sorted(dependencies or []))
            self._audit(conn, project_id, actor, "ticket.create",
                        subject_type="ticket", subject_id=resolved_id,
                        request_id=request_id,
                        summary="created {}".format(resolved_id))
            result = self._serialize_ticket(
                conn, self._ticket_row(conn, project_id, resolved_id)
            )
            self._remember(conn, project_id, request_id, "create_ticket",
                           body, result)
        return result

    def set_dependencies(self, project_id, ticket_id, dependencies,
                         *, expected_version, actor=None, request_id=None):
        actor = actor or SYSTEM_ACTOR
        deps = sorted(dependencies)
        body = {"ticket_id": ticket_id, "dependencies": deps,
                "expected_version": expected_version}
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id,
                                  "set_dependencies", body)
            if replay is not None:
                return replay
            row = self._ticket_row(conn, project_id, ticket_id)
            if row["version"] != expected_version:
                raise TicketVersionConflict(ticket_id, expected_version,
                                            row["version"])
            self._set_dependencies(conn, project_id, ticket_id, deps)
            conn.execute(
                "UPDATE tickets SET version = version + 1, updated_at = ?"
                " WHERE project_id = ? AND id = ?",
                (ids.now(), project_id, ticket_id),
            )
            self._audit(conn, project_id, actor, "ticket.set_dependencies",
                        subject_type="ticket", subject_id=ticket_id,
                        request_id=request_id,
                        summary="dependencies now {}".format(deps or "none"))
            result = self._serialize_ticket(
                conn, self._ticket_row(conn, project_id, ticket_id)
            )
            self._remember(conn, project_id, request_id, "set_dependencies",
                           body, result)
        return result

    def claim_ticket(self, project_id, ticket_id, agent_id, *, expected_version,
                     session_id=None, worktree=None, request_id=None,
                     enforce_dependencies=True):
        """Atomically claim a ticket for an agent.

        Everything below happens inside one BEGIN IMMEDIATE transaction: the
        state check, the capacity count and the write. That is what makes eight
        simultaneous claims produce exactly one winner and seven 409s rather
        than eight winners -- the checks cannot interleave with another
        process's write.
        """
        body = {"ticket_id": ticket_id, "agent_id": agent_id,
                "expected_version": expected_version, "session_id": session_id}
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id, "claim", body)
            if replay is not None:
                return replay

            agent = conn.execute(
                "SELECT * FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
            if agent is None:
                raise NotFound("No such agent.", {"agent_id": agent_id})
            if agent["state"] == "revoked":
                raise SessionLeaseExpired(session_id or "",
                                          {"agent_id": agent_id,
                                           "reason": "agent revoked"})
            if session_id is not None and not self._session_is_current(conn, session_id):
                raise SessionLeaseExpired(session_id, {"agent_id": agent_id})

            row = self._ticket_row(conn, project_id, ticket_id)
            if row["version"] != expected_version:
                raise TicketVersionConflict(ticket_id, expected_version,
                                            row["version"])
            if row["state"] == "claimed":
                raise TicketAlreadyClaimed(ticket_id, row["owner"])
            if row["state"] not in ALLOWED_TRANSITIONS or \
                    "claimed" not in ALLOWED_TRANSITIONS[row["state"]]:
                raise InvalidStateTransition(ticket_id, row["state"], "claimed")

            if enforce_dependencies:
                deps = self._dependencies(conn, project_id, ticket_id)
                unmet = self._unmet_dependencies(conn, project_id, deps)
                if unmet:
                    raise DependencyUnmet(ticket_id, unmet)

            active = conn.execute(
                "SELECT COUNT(*) FROM tickets WHERE owner = ? AND state IN (?, ?)",
                (agent_id,) + ACTIVE_STATES,
            ).fetchone()[0]
            if active >= agent["max_active_tickets"]:
                raise CapacityExhausted(agent_id, active,
                                        agent["max_active_tickets"])

            now = ids.now()
            cur = conn.execute(
                "UPDATE tickets SET state = 'claimed', owner = ?, owner_session = ?,"
                " worktree = COALESCE(?, worktree), claimed_at = ?, updated_at = ?,"
                " last_progress_at = ?, version = version + 1"
                " WHERE project_id = ? AND id = ? AND version = ?",
                (agent_id, session_id, worktree, now, now, now,
                 project_id, ticket_id, expected_version),
            )
            if cur.rowcount != 1:
                # Unreachable while the transaction is IMMEDIATE -- the row was
                # read under the write lock. Asserted anyway: a claim that
                # silently matched no row would report success to an agent that
                # does not own the ticket, which is the one outcome this whole
                # module exists to prevent.
                raise TicketVersionConflict(
                    ticket_id, expected_version,
                    self._ticket_row(conn, project_id, ticket_id)["version"],
                )
            conn.execute(
                "UPDATE agents SET current_ticket = ?, state = 'working',"
                " version = version + 1 WHERE id = ?",
                (ticket_id, agent_id),
            )
            conn.execute(
                "UPDATE assignments SET state = 'claimed', version = version + 1"
                " WHERE project_id = ? AND ticket_id = ? AND agent_id = ?"
                " AND state = 'queued'",
                (project_id, ticket_id, agent_id),
            )
            self._audit(conn, project_id,
                        {"type": "agent", "id": agent_id,
                         "display_name": agent["name"],
                         "session_id": session_id},
                        "ticket.claim", subject_type="ticket",
                        subject_id=ticket_id, request_id=request_id,
                        summary="{} claimed {}".format(agent["name"], ticket_id))
            result = self._serialize_ticket(
                conn, self._ticket_row(conn, project_id, ticket_id)
            )
            self._remember(conn, project_id, request_id, "claim", body, result)
        return result

    def add_update(self, project_id, ticket_id, author, body_text, *,
                   next_step=None, session_id=None, request_id=None):
        """Append a progress update.

        session_id is optional on the wire (T-178/T-224 contract), so absent
        is a THIRD state, never collapsed into "clean": an update is only let
        touch ticket state when it is attributed to the session that
        currently owns the ticket.

        - valid session (attributed, current owner)  -> applies, `ticket.update`
        - stale/foreign session (attributed, not owner) -> kept, denied effect,
          flagged `superseded`, `ticket.update.superseded`
        - no session_id (unattributed) -> kept, denied effect exactly like a
          superseded update (`superseded` stays False on the wire -- the
          contract has no third value for that field -- but it never bumps
          last_progress_at or next_step), audited as
          `ticket.update.unattributed` so recovery decisions can tell "a
          session confirmed this" from "nobody did".

        An absent session_id must not read as equivalent to a valid one: it is
        precisely the shape a displaced session's write takes when the caller
        simply omits the field, and last_progress_at (the 90-minute reopen
        signal) and next_step (what the next owner picks up) are the two
        fields a client could otherwise corrupt for free.
        """
        body = {"ticket_id": ticket_id, "body": body_text,
                "next_step": next_step, "session_id": session_id}
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id, "add_update", body)
            if replay is not None:
                return replay
            row = self._ticket_row(conn, project_id, ticket_id)

            attributed = session_id is not None
            superseded = False
            if attributed:
                if row["owner_session"] is not None and row["owner_session"] != session_id:
                    superseded = True
                elif not self._session_is_current(conn, session_id):
                    superseded = True
            apply_progress = attributed and not superseded

            uid = ids.update_id()
            now = ids.now()
            conn.execute(
                "INSERT INTO ticket_updates (id, project_id, ticket_id, author, body,"
                " next_step, created_at, superseded) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, project_id, ticket_id, _json(author), body_text,
                 next_step, now, 1 if superseded else 0),
            )
            if apply_progress:
                conn.execute(
                    "UPDATE tickets SET last_progress_at = ?, updated_at = ?,"
                    " next_step = COALESCE(?, next_step), version = version + 1"
                    " WHERE project_id = ? AND id = ?",
                    (now, now, next_step, project_id, ticket_id),
                )
                if row["owner"]:
                    conn.execute(
                        "UPDATE agents SET last_progress_at = ?,"
                        " version = version + 1 WHERE id = ?",
                        (now, row["owner"]),
                    )
            if superseded:
                action, summary = ("ticket.update.superseded",
                                    "late update from a superseded session")
            elif not attributed:
                action, summary = ("ticket.update.unattributed",
                                    "progress update with no session_id;"
                                    " not attributed, ticket progress unchanged")
            else:
                action, summary = "ticket.update", "progress update"
            self._audit(conn, project_id, author, action,
                        subject_type="ticket", subject_id=ticket_id,
                        request_id=request_id, summary=summary)
            result = {
                "id": uid, "ticket_id": ticket_id, "author": author,
                "body": body_text, "next_step": next_step,
                "created_at": now, "superseded": superseded,
            }
            self._remember(conn, project_id, request_id, "add_update", body, result)
        return result

    def list_updates(self, project_id, ticket_id):
        rows = self.conn.execute(
            "SELECT * FROM ticket_updates WHERE project_id = ? AND ticket_id = ?"
            " ORDER BY created_at, id",
            (project_id, ticket_id),
        ).fetchall()
        return [
            {
                "id": r["id"], "ticket_id": r["ticket_id"],
                "author": json.loads(r["author"]), "body": r["body"],
                "next_step": r["next_step"], "created_at": r["created_at"],
                "superseded": bool(r["superseded"]),
            }
            for r in rows
        ]

    def transition(self, project_id, ticket_id, to_state, *, expected_version,
                   actor=None, reason=None, request_id=None):
        """Move a ticket between states, enforcing the transition table."""
        actor = actor or SYSTEM_ACTOR
        body = {"ticket_id": ticket_id, "to_state": to_state,
                "expected_version": expected_version, "reason": reason}
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id, "transition", body)
            if replay is not None:
                return replay
            row = self._ticket_row(conn, project_id, ticket_id)
            if row["version"] != expected_version:
                raise TicketVersionConflict(ticket_id, expected_version,
                                            row["version"])
            if to_state not in ALLOWED_TRANSITIONS.get(row["state"], set()):
                raise InvalidStateTransition(ticket_id, row["state"], to_state)
            if to_state == "review" and not json.loads(row["acceptance"]):
                raise MissingAcceptanceCriteria(ticket_id)

            now = ids.now()
            clears_owner = to_state in ("open", "done")
            conn.execute(
                "UPDATE tickets SET state = ?, blocked_reason = ?, updated_at = ?,"
                " owner = CASE WHEN ? THEN NULL ELSE owner END,"
                " owner_session = CASE WHEN ? THEN NULL ELSE owner_session END,"
                " version = version + 1"
                " WHERE project_id = ? AND id = ? AND version = ?",
                (to_state, reason if to_state == "blocked" else None, now,
                 1 if clears_owner else 0, 1 if clears_owner else 0,
                 project_id, ticket_id, expected_version),
            )
            if row["owner"]:
                conn.execute(
                    "UPDATE agents SET current_ticket = CASE WHEN current_ticket = ?"
                    " THEN NULL ELSE current_ticket END,"
                    " state = CASE WHEN current_ticket = ? THEN 'idle' ELSE state END,"
                    " version = version + 1 WHERE id = ? AND ?",
                    (ticket_id, ticket_id, row["owner"], 1 if clears_owner else 0),
                )
            self._audit(conn, project_id, actor, "ticket.transition",
                        subject_type="ticket", subject_id=ticket_id,
                        request_id=request_id,
                        summary="{} -> {}".format(row["state"], to_state))
            result = self._serialize_ticket(
                conn, self._ticket_row(conn, project_id, ticket_id)
            )
            self._remember(conn, project_id, request_id, "transition", body, result)
        return result

    # --------------------------------------------------------------- reviews

    def submit_review(self, project_id, ticket_id, submitted_by, evidence, *,
                      expected_version, notes=None, request_id=None):
        """Submit work for review, pinning the evidence SHA.

        `expected_version` and the "claimed" state check used to live in the
        route handler, read before this method was ever called. That put them
        ahead of `_replay`: submitting bumps the ticket's version, so a
        byte-identical retry read the *new* version in the handler and died as
        a conflict before it ever reached the replay guard here (T-255 -- same
        shape as T-235, a stale precondition instead of a premature write).
        Checked here, after replay, they see the request that actually applies.
        """
        if not isinstance(evidence, dict) or "sha" not in evidence or \
                "branch" not in evidence:
            raise InvalidReviewEvidence(
                "Review evidence needs a branch and a sha.",
                {"ticket_id": ticket_id},
            )
        body = {"ticket_id": ticket_id, "expected_version": expected_version,
                "evidence": evidence, "notes": notes}
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id, "submit_review", body)
            if replay is not None:
                return replay
            row = self._ticket_row(conn, project_id, ticket_id)
            if row["version"] != expected_version:
                raise TicketVersionConflict(ticket_id, expected_version,
                                            row["version"])
            if row["state"] != "claimed":
                raise InvalidStateTransition(ticket_id, row["state"], "review")
            if not json.loads(row["acceptance"]):
                raise MissingAcceptanceCriteria(ticket_id)
            rid = ids.review_id()
            now = ids.now()
            conn.execute(
                "INSERT INTO reviews (id, project_id, ticket_id, state, submitted_by,"
                " submitted_at, evidence, notes) VALUES (?, ?, ?, 'requested', ?, ?, ?, ?)",
                (rid, project_id, ticket_id, _json(submitted_by), now,
                 _json(evidence), notes),
            )
            conn.execute(
                "UPDATE tickets SET state = 'review', evidence = ?, updated_at = ?,"
                " version = version + 1 WHERE project_id = ? AND id = ?",
                (_json(evidence), now, project_id, ticket_id),
            )
            self._audit(conn, project_id, submitted_by, "review.submit",
                        subject_type="review", subject_id=rid,
                        request_id=request_id,
                        summary="review requested for {} at {}".format(
                            ticket_id, evidence.get("sha", "")[:12]))
            result = self.__review(conn, project_id, rid)
            self._remember(conn, project_id, request_id, "submit_review",
                           body, result)
        return result

    def __review(self, conn, project_id, review_id):
        """Scoped by project_id (T-240): review ids are globally unique, but a
        caller-supplied id must never be looked up without also checking the
        project it claims to be acting in -- otherwise two same-named
        projects, whose per-project ticket ids collide by construction, let
        one project's operator reach into another's review. Wrong project and
        nonexistent raise the identical NotFound so neither can be
        distinguished from the other by an attacker (same reason revoke_
        session_lease's agent lookup below does the same).
        """
        row = conn.execute(
            "SELECT * FROM reviews WHERE id = ? AND project_id = ?",
            (review_id, project_id),
        ).fetchone()
        if row is None:
            raise NotFound("No such review.", {"review_id": review_id})
        payload = {
            "id": row["id"], "ticket_id": row["ticket_id"], "state": row["state"],
            "submitted_by": json.loads(row["submitted_by"]),
            "submitted_at": row["submitted_at"],
            "evidence": json.loads(row["evidence"]) if row["evidence"] else None,
            "notes": row["notes"],
            "decided_by": json.loads(row["decided_by"]) if row["decided_by"] else None,
            "decided_at": row["decided_at"],
            "decision_notes": row["decision_notes"],
        }
        return _omit_none(payload, ("notes",))

    def get_review(self, project_id, review_id):
        with read_txn(self.conn) as conn:
            return self.__review(conn, project_id, review_id)

    def decide_review(self, project_id, review_id, decision, decided_by, *,
                      evidence_sha, expected_version, notes=None, request_id=None):
        """Accept or reject a review against the exact submitted artifact.

        `evidence_sha` must equal the SHA pinned at submission. This is the
        T-178 freeze decision 3: re-resolving a branch name at accept time
        would let the tip move between submission and acceptance, which makes
        "done requires acceptance of the reviewed artifact" unenforceable.
        Acceptance additionally refuses if any recorded check failed.

        `expected_version` guards the ticket, not the review, against having
        changed since it was read for the decision. It used to be checked in
        the route handler, ahead of `_replay`: deciding bumps the ticket's
        version, so a byte-identical retry read the *new* version there and
        died as a conflict before replay ever ran (T-255, same shape as
        T-235). Checked here, after replay and before any write, it sees the
        version the retry actually expects.
        """
        if decision not in ("accepted", "rejected"):
            raise MalformedRequest("decision must be accepted or rejected.",
                                   {"decision": decision})
        body = {"review_id": review_id, "decision": decision,
                "evidence_sha": evidence_sha, "expected_version": expected_version,
                "notes": notes}
        with write_txn(self.conn) as conn:
            replay = self._replay(conn, project_id, request_id, "decide_review", body)
            if replay is not None:
                return replay
            review = self.__review(conn, project_id, review_id)
            if review["state"] != "requested":
                raise InvalidStateTransition(review["ticket_id"],
                                             review["state"], decision)
            # `evidence` is None for a review imported from the legacy board
            # with no repository identity (T-224 planner ruling); `evidence_sha`
            # is required on every decision request and so can never itself be
            # None, so this falls through to the same "does not match" refusal
            # rather than a crash on `.get` -- correct, since there is no real
            # sha to accept a decision against.
            pinned = review["evidence"].get("sha") if review["evidence"] else None
            if evidence_sha != pinned:
                raise InvalidReviewEvidence(
                    "Evidence SHA does not match the submitted review.",
                    {"review_id": review_id, "submitted_sha": pinned,
                     "presented_sha": evidence_sha},
                )
            if decision == "accepted":
                failed = [
                    c.get("name")
                    for c in review["evidence"].get("checks", [])
                    if c.get("status") not in ("passed", "skipped")
                ]
                if failed:
                    raise InvalidReviewEvidence(
                        "Cannot accept a review with failing checks.",
                        {"review_id": review_id, "failed_checks": failed},
                    )
            ticket = self._ticket_row(conn, project_id, review["ticket_id"])
            if ticket["version"] != expected_version:
                raise TicketVersionConflict(review["ticket_id"], expected_version,
                                            ticket["version"])
            now = ids.now()
            conn.execute(
                "UPDATE reviews SET state = ?, decided_by = ?, decided_at = ?,"
                " decision_notes = ? WHERE id = ? AND project_id = ?",
                (decision, _json(decided_by), now, notes, review_id, project_id),
            )
            new_state = "done" if decision == "accepted" else "claimed"
            conn.execute(
                "UPDATE tickets SET state = ?, updated_at = ?, version = version + 1,"
                " owner = CASE WHEN ? THEN NULL ELSE owner END,"
                " owner_session = CASE WHEN ? THEN NULL ELSE owner_session END"
                " WHERE project_id = ? AND id = ?",
                (new_state, now, 1 if decision == "accepted" else 0,
                 1 if decision == "accepted" else 0, project_id,
                 review["ticket_id"]),
            )
            if decision == "accepted" and ticket["owner"]:
                conn.execute(
                    "UPDATE agents SET current_ticket = NULL, state = 'idle',"
                    " version = version + 1 WHERE id = ?",
                    (ticket["owner"],),
                )
            self._audit(conn, project_id, decided_by, "review." + decision,
                        subject_type="review", subject_id=review_id,
                        request_id=request_id,
                        summary="{} {} at {}".format(decision,
                                                     review["ticket_id"],
                                                     (pinned or "")[:12]))
            result = self.__review(conn, project_id, review_id)
            self._remember(conn, project_id, request_id, "decide_review",
                           body, result)
        return result

    # ------------------------------------------------------------ assignment

    def assign(self, project_id, ticket_id, agent_id, expires_at, *,
               reason="", lease_epoch=0, actor=None):
        """Queue an assignment for an agent to claim."""
        actor = actor or SYSTEM_ACTOR
        aid = ids.assignment_id()
        with write_txn(self.conn) as conn:
            self._ticket_row(conn, project_id, ticket_id)
            conn.execute(
                "INSERT INTO assignments (id, project_id, ticket_id, agent_id, state,"
                " reason, created_at, expires_at, lease_epoch, version)"
                " VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, 1)",
                (aid, project_id, ticket_id, agent_id, reason, ids.now(),
                 expires_at, lease_epoch),
            )
            self._audit(conn, project_id, actor, "assignment.create",
                        subject_type="assignment", subject_id=aid,
                        summary="{} -> {}".format(ticket_id, agent_id))
            row = conn.execute(
                "SELECT * FROM assignments WHERE id = ?", (aid,)
            ).fetchone()
            return dict(row)

    # ---------------------------------------------------------------- import

    # Import-only writes. These bypass the transition table on purpose: a
    # legacy board legitimately contains tickets that are already done or
    # blocked, and replaying them through the normal state machine would either
    # reject them or fabricate a transition history that never happened. They
    # are still audited, and they are not exported in __all__ for route use.

    def import_set_state(self, project_id, ticket_id, state, *, owner_name=None,
                         created_at=None, updated_at=None):
        with write_txn(self.conn) as conn:
            self._ticket_row(conn, project_id, ticket_id)
            conn.execute(
                "UPDATE tickets SET state = ?, handoff = COALESCE(?, handoff),"
                " created_at = COALESCE(?, created_at),"
                " updated_at = COALESCE(?, updated_at), version = version + 1"
                " WHERE project_id = ? AND id = ?",
                (state, owner_name, created_at, updated_at, project_id, ticket_id),
            )
            self._audit(conn, project_id, SYSTEM_ACTOR, "ticket.import",
                        subject_type="ticket", subject_id=ticket_id,
                        summary="imported as {}".format(state))

    def import_set_dependencies(self, project_id, ticket_id, dependencies):
        with write_txn(self.conn) as conn:
            self._ticket_row(conn, project_id, ticket_id)
            self._set_dependencies(conn, project_id, ticket_id, sorted(dependencies))
            conn.execute(
                "UPDATE tickets SET version = version + 1"
                " WHERE project_id = ? AND id = ?",
                (project_id, ticket_id),
            )
            self._audit(conn, project_id, SYSTEM_ACTOR, "ticket.import_dependencies",
                        subject_type="ticket", subject_id=ticket_id,
                        summary="imported {} dependencies".format(len(dependencies)))
