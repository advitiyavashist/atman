"""The board API: routing, authorization and the handlers.

The whole surface is one `handle(request) -> Response` function. It is pure
with respect to the network, so almost every test drives it directly and only
the behaviours where the socket *is* the behaviour -- SSE reconnect, the
cross-process claim race -- need `httpd.py`.

Authorization is decided in exactly one place, `_authorize`, before any handler
runs. That is deliberate: a per-handler check is a check somebody forgets to
add to the next route, and the two credential types differ in what they may do,
not merely in who they are.

Routes belonging to other lanes (`/messages`, `/channels`, `/members`,
`/invitations`, `/runners`, `/runs`) are declared here and answer 404 with the
contract's error shape and the owning ticket named in the message. Returning a
contract-shaped refusal that says "T-187 owns this" is more useful to the
console lane than a bare connection error, and it cannot be mistaken for a
working route.
"""

from __future__ import annotations

import re
import sqlite3
import traceback

from ..storage import BoardStore, ids
from ..storage.db import write_txn
from . import hooks, master, validate, views
from .auth import (
    SESSION_LEASE_SECONDS,
    CredentialStore,
    check_csrf,
    in_scope,
    in_seconds,
)
from .errors import (
    AgentTokenInsufficient,
    AssignmentExpired,
    BoardError,
    ForbiddenScope,
    InvalidStateTransition,
    MalformedRequest,
    NotFound,
    SessionLeaseExpired,
    TicketVersionConflict,
    Unauthenticated,
)
from .events import EventStream
from .schema import apply_server_schema
from .wire import Response

PROJECT_ID_RE = re.compile(r"^prj_[0-9a-z]{8,32}$")
SESSION_ID_RE = re.compile(r"^ses_[0-9a-z]{8,32}$")
REVIEW_ID_RE = re.compile(r"^rev_[0-9a-z]{8,32}$")
AGENT_ID_RE = re.compile(r"^agt_[0-9a-z]{8,32}$")
ASSIGNMENT_ID_RE = re.compile(r"^asg_[0-9a-z]{8,32}$")

# Auth requirements, resolved before the handler runs.
ANY = "any"            # either credential type
OPERATOR = "operator"  # agent tokens get 403 agent_token_insufficient
AGENT = "agent"        # an operator has no runtime session to act through
NONE = "none"          # no credential is read; the body carries the proof


class Ctx:
    def __init__(self, request, principal, project_id, params):
        self.request = request
        self.principal = principal
        self.project_id = project_id
        self.params = params
        self._body = None

    def body(self):
        if self._body is None:
            self._body = self.request.json_body()
        return self._body


class BoardServer:
    """Owns the store, the credentials and the stream for one board file."""

    def __init__(self, db_path, *, allowed_origins=None, base_url=None,
                 session_lease_seconds=SESSION_LEASE_SECONDS,
                 rate_limiter=None, event_stream=None):
        self.store = BoardStore(db_path)
        apply_server_schema(self.store.conn)
        self.credentials = CredentialStore(self.store)
        self.events = event_stream or EventStream(self.store)
        self.rate_limiter = rate_limiter or hooks.RateLimiter()
        self.base_url = base_url or "http://127.0.0.1:4319"
        self.allowed_origins = set(allowed_origins or [
            self.base_url, "http://127.0.0.1:4319", "http://localhost:4319",
        ])
        self.session_lease_seconds = session_lease_seconds

    def close(self):
        self.store.close()

    # ------------------------------------------------------------- bootstrap

    def bootstrap_operator(self, project_id, display_name="Operator", role="owner"):
        """Mint the first operator session for a board.

        The frozen contract has no operator sign-in route, so a dashboard
        session has to start out of band. On a loopback V1 that is the honest
        shape anyway: whoever can read the server's own state directory is
        already the operator. `httpd.serve` writes the pair to a 0600 file and
        prints the URL, the way a local notebook server hands out its token.
        Raised in docs/api-notes.md as a contract gap rather than fixed here.
        """
        operator = self.credentials.create_operator(project_id, display_name,
                                                    role=role)
        return self.credentials.open_operator_session(operator)

    # ---------------------------------------------------------------- router

    def handle(self, request):
        request_id = _request_id_of(request)
        try:
            handler, params, auth, needs_project = self._match(request)
            project_id = self._project_id(request) if needs_project else None
            principal = self._authorize(request, auth, project_id)
            ctx = Ctx(request, principal, project_id, params)
            response = handler(ctx)
        except BoardError as err:
            response = Response(err.status, err.to_error_response(request_id))
        except Exception:
            # Anything that reaches here is a bug in this server, not a client
            # error. It still has to become a response: letting it escape drops
            # the connection, and a dashboard cannot tell that apart from the
            # board being down.
            #
            # Note the honest wart -- `ErrorResponse.status` is a closed enum of
            # 400/401/403/404/409/422/429, so this body cannot validate against
            # the contract. The frozen enum has no member for "we failed", and
            # inventing one here would be a unilateral amendment. Named in
            # docs/api-notes.md instead. The message is deliberately free of
            # detail: an exception string can carry a path, a query or a token.
            traceback.print_exc()
            response = Response(500, {"error": {
                "code": "internal_error", "status": 500,
                "message": "The board server failed to handle this request.",
            }})
        if request_id:
            response.headers.setdefault("X-Request-Id", request_id)
        return response

    def _match(self, request):
        for method, pattern, name, auth, needs_project in self._routes():
            found = pattern.match(request.path)
            if not found:
                continue
            if method != request.method:
                continue
            return getattr(self, name), found.groupdict(), auth, needs_project
        # Any method mismatch lands here too. 405 is not in the contract's
        # status enum, so an unroutable request is a contract-shaped 404
        # rather than a status the published error schema forbids.
        raise NotFound("No such route.", {"path": request.path})

    def _routes(self):
        if getattr(self, "_compiled", None) is None:
            self._compiled = [
                (m, re.compile(p), h, a, s) for m, p, h, a, s in _ROUTE_TABLE
            ]
        return self._compiled

    def _project_id(self, request):
        value = request.headers.get("x-project-id")
        if not value or not PROJECT_ID_RE.match(value):
            raise MalformedRequest("X-Project-Id header is required.",
                                   {"missing_fields": ["X-Project-Id"]})
        return value

    def _authorize(self, request, auth, project_id):
        """Pick the credential this route accepts, then check it.

        `security` in the contract is a list of alternatives, so a request that
        presents both an operator session and an agent token satisfies a route
        if *either* fits it. Resolving one credential and then asking whether it
        happens to fit refuses callers who did present an acceptable one -- and
        it is not more secure, because both credentials belong to the caller and
        were each validated on their own.
        """
        if auth == NONE:
            # This route carries its own proof in the body -- the enrollment
            # code -- so it authenticates nothing, and a credential that happens
            # to be attached is not part of the decision: not to refuse the
            # request over it (an agent whose lease was revoked has a dead
            # token in its configured headers *by construction*, and
            # re-enrolment is how it comes back -- it must not have to know to
            # strip its own header first), and, per T-264, not to hand it to
            # the handler either. Resolving a credential here and filtering it
            # by in_scope() would still expose an authenticated identity from a
            # route whose contract is that no authentication decision is made.
            # A handler behind auth=NONE that needs caller identity has to read
            # its own proof from the body, the same shape as this route's
            # enrollment code -- never from ctx.principal, which is always None
            # here regardless of what credential (valid, foreign, or dead) was
            # attached.
            return None

        principals = self.credentials.authenticate_all(request)
        if not principals:
            raise Unauthenticated()

        scoped = [p for p in principals if in_scope(p, project_id)]
        if not scoped:
            raise ForbiddenScope()

        candidates = [p for p in scoped if _satisfies(p, auth)]
        if not candidates:
            if auth == OPERATOR:
                raise AgentTokenInsufficient("{} {}".format(request.method,
                                                            request.path))
            raise ForbiddenScope(
                "This route acts through a runtime session; use an agent credential."
            )
        principal = candidates[0]
        check_csrf(principal, request, allowed_origins=self.allowed_origins)
        return principal

    # ------------------------------------------------------------- overview

    def get_overview(self, ctx):
        project = self.store.get_project(ctx.project_id)
        tickets = self.store.list_tickets(ctx.project_id)
        agents = views_list_agents(self.store, ctx.project_id)
        master.expire_assignments(self.store, ctx.project_id)
        lease = views.serialize_master_lease(
            master.lease_row(self.store, ctx.project_id))
        stream = views.stream_status(self.store, ctx.project_id)
        payload = {
            "project": project,
            "counts": views.overview_counts(tickets),
            "attention": views.attention_items(self.store, ctx.project_id,
                                               tickets, agents, lease),
            "recent_accepted": self._recent_accepted(ctx.project_id),
            "master": lease,
            "stream": stream,
        }
        if not tickets and not agents:
            payload["empty_state"] = dict(views.EMPTY_STATES["overview"])
        return Response(200, payload, headers={
            "X-Snapshot-Version": str(stream["snapshot_version"]),
        })

    def _recent_accepted(self, project_id, limit=5):
        rows = self.store.conn.execute(
            "SELECT id FROM reviews WHERE project_id = ? AND state = 'accepted'"
            " ORDER BY decided_at DESC, rowid DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [self.store.get_review(project_id, row["id"]) for row in rows]

    # -------------------------------------------------------------- tickets

    def list_tickets(self, ctx):
        request = ctx.request
        state = request.param("state")
        if state is not None and state not in ("open", "claimed", "review",
                                               "done", "blocked"):
            # `dependency_blocked` is the one people try here. It is a derived
            # boolean with its own query parameter, not a sixth state.
            raise MalformedRequest(
                "state must be one of open, claimed, review, done, blocked.",
                {"rejected_fields": ["state"]},
            )
        items = self.store.list_tickets(ctx.project_id, state=state)
        role = request.param("role")
        owner = request.param("owner")
        needle = (request.param("q") or "").lower()
        blocked_flag = request.param("dependency_blocked")

        if role is not None:
            items = [t for t in items if t.get("role") == role]
        if owner is not None:
            items = [t for t in items if t.get("owner") == owner]
        if needle:
            items = [t for t in items
                     if needle in t["title"].lower()
                     or needle in t["id"].lower()
                     or needle in (t.get("outcome") or "").lower()]
        if blocked_flag is not None:
            want = _query_bool(blocked_flag, "dependency_blocked")
            items = [t for t in items if t["dependency_blocked"] is want]

        total = len(items)
        after = views.decode_cursor("tickets", request.param("cursor"))
        if after is not None:
            items = [t for t in items if t["id"] > after]
        limit = views.page_limit(request.param("limit"))
        page, more = items[:limit], len(items) > limit

        payload = {
            "items": page,
            "next_cursor": views.encode_cursor("tickets", page[-1]["id"]) if more else None,
            "total_matching": total,
            "stream": views.stream_status(self.store, ctx.project_id),
        }
        if total == 0:
            payload["empty_state"] = dict(views.EMPTY_STATES["tickets"])
        return Response(200, payload)

    def create_ticket(self, ctx):
        body = validate.check_body(
            ctx.body(),
            required=("request_id", "title", "outcome", "acceptance"),
            allowed=("role", "dependencies", "files"),
        )
        request_id = validate.request_id(body)
        ticket = self.store.create_ticket(
            ctx.project_id,
            lambda conn: self._next_ticket_id(ctx.project_id, conn),
            validate.text(body, "title", max_length=200),
            role=validate.text(body, "role", max_length=40, required=False),
            outcome=validate.text(body, "outcome", max_length=4000),
            acceptance=validate.acceptance(body),
            dependencies=validate.string_list(body, "dependencies", max_length=22),
            files=validate.string_list(body, "files"),
            actor=ctx.principal.actor,
            request_id=request_id,
        )
        return Response(201, ticket)

    def _next_ticket_id(self, project_id, conn=None):
        """Mint the next human-facing key for a project.

        `CreateTicketRequest` has no id field, so the server owns the key. The
        prefix comes from the project name so a board reads the way its
        operators talk about it; the number is the highest existing plus one,
        which keeps ids stable and gapless enough to cite in conversation.

        Called from inside store.create_ticket's transaction, after its replay
        check -- see that method's docstring for why (T-235).
        """
        conn = conn if conn is not None else self.store.conn
        project = self.store.get_project(project_id)
        letters = "".join(c for c in project["name"].upper() if c.isalnum())
        prefix = letters[:16] if letters[:1].isalpha() else "TB"
        if len(prefix) < 2:
            prefix = (prefix + "TB")[:2]
        rows = conn.execute(
            "SELECT id FROM tickets WHERE project_id = ? AND id LIKE ?",
            (project_id, prefix + "-%"),
        ).fetchall()
        highest = 0
        for row in rows:
            suffix = row["id"].rsplit("-", 1)[-1]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
        return "{}-{}".format(prefix, highest + 1)

    def get_ticket(self, ctx):
        ticket_id = validate.ticket_id(ctx.params["ticket_id"])
        ticket = self.store.get_ticket(ctx.project_id, ticket_id)
        reviews = self._reviews_for(ctx.project_id, ticket_id)
        pending = next((r for r in reviews if r["state"] == "requested"), None)
        master.expire_assignments(self.store, ctx.project_id)
        assignment = views.serialize_assignment(
            master.queued_assignment(self.store, ctx.project_id, ticket_id))
        payload = {
            "ticket": ticket,
            "updates": self.store.list_updates(ctx.project_id, ticket_id),
            "reviews": reviews,
            "assignment": assignment,
            "dependencies": self._dependency_summaries(ctx.project_id, ticket),
            "available_actions": views.available_actions(
                ticket, ctx.principal, pending_review=pending,
                is_master=self._is_master(ctx)),
            "stream": views.stream_status(self.store, ctx.project_id),
        }
        return Response(200, payload)

    def _dependency_summaries(self, project_id, ticket):
        """Resolved so the console does not have to issue one read per edge."""
        summaries = []
        for dep in ticket.get("dependencies", []):
            row = self.store.conn.execute(
                "SELECT id, title, state FROM tickets WHERE project_id = ? AND id = ?",
                (project_id, dep),
            ).fetchone()
            if row is None:
                # An edge to a ticket that is not on this board is reported,
                # not dropped: it is why the dependent is blocked.
                summaries.append({"id": dep, "title": "Not on this board",
                                  "state": "open"})
            else:
                summaries.append({"id": row["id"], "title": row["title"],
                                  "state": row["state"]})
        return summaries

    def _reviews_for(self, project_id, ticket_id):
        rows = self.store.conn.execute(
            "SELECT id FROM reviews WHERE project_id = ? AND ticket_id = ?"
            " ORDER BY submitted_at, rowid",
            (project_id, ticket_id),
        ).fetchall()
        return [self.store.get_review(project_id, row["id"]) for row in rows]

    def _is_master(self, ctx):
        row = master.lease_row(self.store, ctx.project_id)
        if row is None or not row["holder"] or row["expires_at"] <= ids.now():
            return False
        import json as _json
        holder = _json.loads(row["holder"])
        return holder.get("id") == ctx.principal.id

    def claim_ticket(self, ctx):
        body = validate.check_body(
            ctx.body(),
            required=("request_id", "expected_version", "session_id"),
            allowed=("assignment_id",),
        )
        request_id = validate.request_id(body)
        expected_version = validate.integer(body, "expected_version", minimum=0)
        session_id = validate.text(body, "session_id", max_length=36)
        if not SESSION_ID_RE.match(session_id):
            raise MalformedRequest("session_id must look like ses_...",
                                   {"rejected_fields": ["session_id"]})
        ticket_id = validate.ticket_id(ctx.params["ticket_id"])
        self._require_live_lease(ctx.principal, session_id)

        assignment_id = body.get("assignment_id")
        if assignment_id is not None:
            self._check_assignment(ctx, assignment_id, ticket_id)

        ticket = self.store.claim_ticket(
            ctx.project_id, ticket_id, ctx.principal.agent_id,
            expected_version=expected_version, session_id=session_id,
            request_id=request_id,
        )
        return Response(200, ticket)

    def _require_live_lease(self, principal, session_id):
        """The check the store cannot make: a lease that has *expired*.

        `BoardStore._session_is_current` tests only that a lease was not
        revoked, which is the right question for "is this update superseded".
        It is the wrong question for a claim: the contract's release bar is that
        an agent whose adapter has stopped delivering events cannot claim
        offline, and that agent's lease is expired, not revoked.
        """
        lease = self.store.conn.execute(
            "SELECT * FROM session_leases WHERE session_id = ?", (session_id,)
        ).fetchone()
        if lease is None or lease["agent_id"] != principal.agent_id:
            raise SessionLeaseExpired(session_id,
                                      {"reason": "no such session for this agent"})
        if lease["revoked_at"] is not None:
            raise SessionLeaseExpired(session_id, {"reason": "revoked"})
        if lease["expires_at"] <= ids.now():
            raise SessionLeaseExpired(session_id,
                                      {"reason": "lease expired",
                                       "expires_at": lease["expires_at"]})

    def _check_assignment(self, ctx, assignment_id, ticket_id):
        if not ASSIGNMENT_ID_RE.match(str(assignment_id)):
            raise MalformedRequest("assignment_id must look like asg_...",
                                   {"rejected_fields": ["assignment_id"]})
        master.expire_assignments(self.store, ctx.project_id)
        row = master.get_assignment(self.store, ctx.project_id, assignment_id)
        if row is None or row["ticket_id"] != ticket_id or \
                row["agent_id"] != ctx.principal.agent_id:
            raise NotFound("No such reservation for this ticket and agent.",
                           {"assignment_id": assignment_id})
        if row["state"] != "queued":
            raise AssignmentExpired(assignment_id, row["expires_at"])

    def create_ticket_update(self, ctx):
        body = validate.check_body(
            ctx.body(),
            required=("request_id", "body", "next_step"),
            allowed=("session_id",),
        )
        request_id = validate.request_id(body)
        ticket_id = validate.ticket_id(ctx.params["ticket_id"])
        session_id = validate.text(body, "session_id", max_length=36, required=False)
        ticket = self.store.get_ticket(ctx.project_id, ticket_id)
        if ctx.principal.is_agent and ticket.get("owner") not in (
                None, ctx.principal.agent_id):
            # The displaced-owner case from the 12:30Z ruling: the ticket has
            # been reassigned to somebody else, and this agent must be told so
            # rather than left writing into a ticket it no longer holds.
            #
            # This is a different axis from the store's `superseded` flag, and
            # both are wanted. Superseded is about a stale *session* of the
            # agent that still owns the ticket -- that update is kept, because
            # a displaced session's account of what it was doing is the most
            # useful thing in the trail after a takeover. This check is about a
            # different *agent* entirely, where there is no such account to
            # keep and silence is what let the real incident run for 10 min.
            raise ForbiddenScope(
                "This ticket is no longer yours; it is owned by another agent.")

        author = dict(ctx.principal.actor)
        if session_id:
            author["session_id"] = session_id
        update = self.store.add_update(
            ctx.project_id, ticket_id, author,
            validate.text(body, "body", max_length=4000),
            next_step=validate.text(body, "next_step", max_length=1000),
            session_id=session_id,
            request_id=request_id,
        )
        return Response(201, update)

    def request_review(self, ctx):
        body = validate.check_body(
            ctx.body(),
            required=("request_id", "expected_version", "evidence"),
            allowed=("notes",),
        )
        request_id = validate.request_id(body)
        ticket_id = validate.ticket_id(ctx.params["ticket_id"])
        expected_version = validate.integer(body, "expected_version", minimum=0)
        evidence = validate.git_evidence(body)

        ticket = self.store.get_ticket(ctx.project_id, ticket_id)
        _require_version(ticket_id, expected_version, ticket["version"])
        if ticket["state"] != "claimed":
            raise InvalidStateTransition(ticket_id, ticket["state"], "review")
        if ctx.principal.is_agent and ticket.get("owner") != ctx.principal.agent_id:
            raise ForbiddenScope("Only the ticket's owner can submit it for review.")

        review = self.store.submit_review(
            ctx.project_id, ticket_id, ctx.principal.actor, evidence,
            notes=validate.text(body, "notes", max_length=4000, required=False),
            request_id=request_id,
        )
        return Response(201, review)

    def decide_review(self, ctx):
        body = validate.check_body(
            ctx.body(),
            required=("request_id", "expected_version", "decision", "evidence_sha"),
            allowed=("notes",),
        )
        request_id = validate.request_id(body)
        ticket_id = validate.ticket_id(ctx.params["ticket_id"])
        review_id = ctx.params["review_id"]
        if not REVIEW_ID_RE.match(review_id):
            raise NotFound("No such review.", {"review_id": review_id})
        decision = validate.enum(body, "decision", ("accept", "reject"))
        evidence_sha = validate.sha(body, "evidence_sha")
        expected_version = validate.integer(body, "expected_version", minimum=0)

        # Scoped by ctx.project_id (T-240): review_id is a caller-supplied id
        # and ticket_id can collide across projects (per-project highest+1),
        # so an unscoped lookup here let an operator in project A decide a
        # review that actually belongs to project B's same-named ticket.
        review = self.store.get_review(ctx.project_id, review_id)
        if review["ticket_id"] != ticket_id:
            raise NotFound("No such review on this ticket.",
                           {"review_id": review_id, "ticket_id": ticket_id})
        # The reviewer must differ from the submitter, and the master cannot
        # accept its own implementation. Both are the same check: acceptance by
        # the author is not review, whatever role the author is wearing.
        if (review.get("submitted_by") or {}).get("id") == ctx.principal.id:
            raise ForbiddenScope("A review cannot be decided by its submitter.")
        ticket = self.store.get_ticket(ctx.project_id, ticket_id)
        _require_version(ticket_id, expected_version, ticket["version"])

        decided = self.store.decide_review(
            ctx.project_id, review_id,
            "accepted" if decision == "accept" else "rejected",
            ctx.principal.actor,
            evidence_sha=evidence_sha,
            notes=validate.text(body, "notes", max_length=4000, required=False),
            request_id=request_id,
        )
        return Response(200, decided)

    def set_ticket_blocked(self, ctx):
        body = validate.check_body(
            ctx.body(),
            required=("request_id", "expected_version", "blocked"),
            allowed=("reason",),
        )
        request_id = validate.request_id(body)
        ticket_id = validate.ticket_id(ctx.params["ticket_id"])
        expected_version = validate.integer(body, "expected_version", minimum=0)
        blocked = validate.boolean(body, "blocked")
        reason = validate.text(body, "reason", max_length=1000, required=False)
        if blocked and not reason:
            raise MalformedRequest("reason is required when blocking.",
                                   {"missing_fields": ["reason"]})

        ticket = self.store.get_ticket(ctx.project_id, ticket_id)
        if ctx.principal.is_agent and ticket.get("owner") not in (
                None, ctx.principal.agent_id):
            raise ForbiddenScope("Only the ticket's owner or an operator can "
                                 "change its blocked state.")
        if blocked:
            to_state = "blocked"
        else:
            # Unblocking returns the ticket to where it can be worked: back to
            # its owner if it still has one, otherwise to the open queue.
            to_state = "claimed" if ticket.get("owner") else "open"
        result = self.store.transition(
            ctx.project_id, ticket_id, to_state,
            expected_version=expected_version, actor=ctx.principal.actor,
            reason=reason, request_id=request_id,
        )
        return Response(200, result)

    # --------------------------------------------------------------- agents

    def list_agents(self, ctx):
        items = views_list_agents(self.store, ctx.project_id)
        payload = {
            "items": items,
            "stream": views.stream_status(self.store, ctx.project_id),
        }
        if not items:
            payload["empty_state"] = dict(views.EMPTY_STATES["agents"])
        return Response(200, payload)

    def revoke_session_lease(self, ctx):
        body = validate.check_body(
            ctx.body(), required=("request_id", "expected_version", "note"),
        )
        request_id = validate.request_id(body)
        agent_id = ctx.params["agent_id"]
        if not AGENT_ID_RE.match(agent_id):
            raise NotFound("No such agent in this project.", {"agent_id": agent_id})
        note = validate.text(body, "note", max_length=1000)
        expected_version = validate.integer(body, "expected_version", minimum=0)

        # Scoped by ctx.project_id (T-240): an unscoped get_agent() here,
        # followed by a project check that raised a *different* error
        # (ForbiddenScope, 403) than "no such agent" (NotFound, 404), let an
        # attacker learn whether an agent_id exists in another project from
        # the status code alone even though the revoke itself was blocked.
        # get_agent(project_id=...) now raises the identical NotFound either
        # way, so existence in another project is not observable.
        agent = self.store.get_agent(agent_id, ctx.project_id)
        if agent["version"] != expected_version:
            raise _version_conflict("agent_id", agent_id, expected_version,
                                    agent["version"])
        lease = views.latest_session(self.store, agent_id)
        if lease is None:
            raise NotFound("That agent has no session lease.",
                           {"agent_id": agent_id})
        if lease["revoked_at"] is not None:
            raise SessionLeaseExpired(lease["session_id"],
                                      {"reason": "already revoked",
                                       "revoked_at": lease["revoked_at"]})

        self.store.revoke_session(lease["session_id"], note=note)
        # The credential goes with the lease. A revocation that leaves a live
        # bearer token behind has not stopped the session it named.
        self.credentials.revoke_agent_tokens_for_session(lease["session_id"])
        self.store.conn.execute(
            "UPDATE agents SET state = 'revoked', version = version + 1 WHERE id = ?",
            (agent_id,),
        )
        # `request_id` is validated above but cannot reach the audit row:
        # `BoardStore.revoke_session` takes no request_id. Named in
        # docs/api-notes.md rather than silently dropped.
        #
        # Work is preserved and the ticket is NOT reassigned here; a new claim
        # is a separate, explicit step.
        return Response(200, views.serialize_agent(
            self.store, self.store.get_agent(agent_id, ctx.project_id)))

    def create_enrollment(self, ctx):
        body = validate.check_body(
            ctx.body(),
            required=("request_id", "agent_name", "role"),
            allowed=("capabilities", "worktree", "connection_mode",
                     "max_active_tickets"),
        )
        validate.request_id(body)
        name = validate.text(body, "agent_name", max_length=80)
        role = validate.text(body, "role", max_length=40)
        connection_mode = validate.enum(body, "connection_mode",
                                        ("managed", "hook_only"),
                                        required=False, default="managed")
        max_active = validate.integer(body, "max_active_tickets", minimum=1,
                                      default=1)
        capabilities = validate.string_list(body, "capabilities", max_length=40)
        worktree = validate.text(body, "worktree", max_length=300, required=False)

        try:
            agent = self.store.create_agent(
                ctx.project_id, name, role=role, capabilities=capabilities,
                connection_mode=connection_mode, max_active_tickets=max_active,
            )
        except sqlite3.IntegrityError:
            # UNIQUE(project_id, name). No frozen 409 code fits a duplicate
            # agent name, and inventing one would break the error enum, so this
            # is a 400 that says exactly what to change.
            raise MalformedRequest(
                "An agent named {} already exists in this project.".format(name),
                {"rejected_fields": ["agent_name"]},
            )
        if worktree:
            self.store.conn.execute(
                "UPDATE agents SET worktree = ? WHERE id = ?", (worktree, agent["id"])
            )
            agent = self.store.get_agent(agent["id"])

        enrollment = self.credentials.create_enrollment(ctx.project_id, agent["id"])
        return Response(201, {
            "enrollment_id": enrollment["enrollment_id"],
            "agent": views.serialize_agent(self.store, agent),
            "expires_at": enrollment["expires_at"],
            # Returned exactly once. It is never written to the database (only
            # its hash is), never logged and never put in a URL.
            "code": enrollment["code"],
            "install_command": "tickets connect --project {} --server {}".format(
                ctx.project_id, self.base_url),
            "config_changes": _CONFIG_CHANGES,
        })

    def exchange_enrollment(self, ctx):
        body = validate.check_body(
            ctx.body(), required=("request_id", "code", "session_id"),
            allowed=("runtime",),
        )
        validate.request_id(body)
        session_id = validate.text(body, "session_id", max_length=36)
        if not SESSION_ID_RE.match(session_id):
            raise MalformedRequest("session_id must look like ses_...",
                                   {"rejected_fields": ["session_id"]})
        project_id = ctx.project_id
        if self.store.conn.execute(
                "SELECT 1 FROM session_leases WHERE session_id = ?",
                (session_id,)).fetchone() is not None:
            # Checked before the code is spent. A session id collision after
            # redemption would burn a single-use code on a request that cannot
            # succeed, and leave the agent with no way to retry.
            raise _session_id_taken(session_id)
        redeemed = self.credentials.consume_enrollment(project_id,
                                                       body.get("code"))
        agent_id = redeemed["agent_id"]

        try:
            lease = self.store.open_session(
                agent_id, in_seconds(self.session_lease_seconds),
                session_id=session_id)
        except sqlite3.IntegrityError:
            # The pre-check above is not the whole guard: session_leases has
            # session_id as its primary key, and two exchanges racing on the
            # same id can both pass the check before either inserts. The loser
            # arrives here having already spent its code, so it cannot be made
            # retryable -- but it must still leave as a contract-shaped 400
            # naming the field, not an unhandled IntegrityError as a 500.
            raise _session_id_taken(session_id)
        runtime = body.get("runtime") or {}
        if not isinstance(runtime, dict) or set(runtime) - {"adapter", "version"}:
            raise MalformedRequest("runtime accepts adapter and version only.",
                                   {"rejected_fields": ["runtime"]})
        self._mark_installed(agent_id, runtime)
        token = self.credentials.issue_agent_token(agent_id, project_id,
                                                   session_id=session_id)
        return Response(201, {
            "agent": views.serialize_agent(self.store,
                                           self.store.get_agent(agent_id)),
            "token": token,
            "lease": lease,
        })

    def _mark_installed(self, agent_id, runtime):
        """Exchanging the code proves the install command ran.

        `config_installed` cannot be reported through `HookEventRequest` -- the
        event shape is `additionalProperties: false` and has no field for it --
        so this is where the server learns it, and it is the only claim the
        exchange justifies. The other three booleans stay false until a real
        event arrives.
        """
        import json as _json

        row = self.store.conn.execute(
            "SELECT hook_health FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        health = _json.loads(row["hook_health"] or "{}")
        health["config_installed"] = True
        health.setdefault("server_received", False)
        health.setdefault("response_delivered", False)
        health.setdefault("session_adopted", False)
        health.setdefault("last_error", None)
        stored_runtime = {"adapter": runtime.get("adapter", "claude_code"),
                          "version": runtime.get("version"),
                          "unsupported_capabilities": []}
        stored_runtime = {k: v for k, v in stored_runtime.items() if v is not None}
        with write_txn(self.store.conn) as conn:
            conn.execute(
                "UPDATE agents SET hook_health = ?, runtime = ?,"
                " version = version + 1 WHERE id = ?",
                (_json.dumps(health), _json.dumps(stored_runtime), agent_id),
            )

    def post_hook_event(self, ctx):
        body = validate.check_body(ctx.body(), required=("request_id", "event"))
        request_id = validate.request_id(body)
        event = hooks.validate_event(body.get("event"))
        if event["agent_id"] != ctx.principal.agent_id:
            raise ForbiddenScope("A hook event may only be posted for its own agent.")
        self.rate_limiter.check(ctx.principal.agent_id)

        deduplicated, agent = hooks.record(
            self.store, ctx.project_id, ctx.principal, event,
            request_id=request_id, lease_seconds=self.session_lease_seconds)
        return Response(200, {
            "accepted": True,
            "deduplicated": deduplicated,
            "context": hooks.context_for(self.store, ctx.project_id, agent, event),
        })

    # --------------------------------------------------------------- master

    def get_master_panel(self, ctx):
        master.expire_assignments(self.store, ctx.project_id)
        return Response(200, {
            "lease": views.serialize_master_lease(
                master.lease_row(self.store, ctx.project_id)),
            "queue": [views.serialize_assignment(row)
                      for row in master.queue(self.store, ctx.project_id)],
            "decisions": master.decisions(self.store, ctx.project_id),
            "stream": views.stream_status(self.store, ctx.project_id),
        })

    def take_master_lease(self, ctx):
        body = validate.check_body(ctx.body(),
                                   required=("request_id", "expected_epoch"),
                                   allowed=("ttl_seconds",))
        request_id = validate.request_id(body)
        lease = master.take_lease(
            self.store, ctx.project_id, ctx.principal.as_master(),
            expected_epoch=validate.integer(body, "expected_epoch", minimum=0),
            ttl_seconds=validate.integer(body, "ttl_seconds", minimum=15,
                                         maximum=600, default=120),
            request_id=request_id,
        )
        return Response(200, lease)

    def set_master_paused(self, ctx):
        body = validate.check_body(
            ctx.body(), required=("request_id", "lease_epoch", "paused"))
        request_id = validate.request_id(body)
        lease = master.set_paused(
            self.store, ctx.project_id, ctx.principal.actor,
            lease_epoch=validate.integer(body, "lease_epoch", minimum=0),
            paused=validate.boolean(body, "paused"),
            request_id=request_id,
        )
        return Response(200, lease)

    def create_assignment(self, ctx):
        body = validate.check_body(
            ctx.body(),
            required=("request_id", "ticket_id", "agent_id", "reason",
                      "lease_epoch", "expected_version"),
            allowed=("ttl_seconds",),
        )
        request_id = validate.request_id(body)
        assignment = master.create_assignment(
            self.store, ctx.project_id, ctx.principal.actor,
            ticket_id=validate.ticket_id(body.get("ticket_id")),
            agent_id=validate.text(body, "agent_id", max_length=36),
            reason=validate.text(body, "reason", max_length=1000),
            lease_epoch=validate.integer(body, "lease_epoch", minimum=0),
            expected_version=validate.integer(body, "expected_version", minimum=0),
            ttl_seconds=validate.integer(body, "ttl_seconds", minimum=30,
                                         maximum=3600, default=600),
            request_id=request_id,
        )
        return Response(201, assignment)

    # ------------------------------------------------------- activity, SSE

    def list_activity(self, ctx):
        limit = views.page_limit(ctx.request.param("limit"))
        after = views.decode_cursor("activity", ctx.request.param("cursor")) or 0
        subject_type = ctx.request.param("subject_type")
        sql = ("SELECT * FROM audit_events WHERE project_id = ? AND seq > ?")
        args = [ctx.project_id, after]
        if subject_type:
            sql += " AND subject_type = ?"
            args.append(subject_type)
        sql += " ORDER BY seq LIMIT ?"
        args.append(limit + 1)
        rows = self.store.conn.execute(sql, args).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        items = [_audit_event(row) for row in rows]
        payload = {
            "items": items,
            "next_cursor": views.encode_cursor("activity", rows[-1]["seq"]) if more else None,
            "stream": views.stream_status(self.store, ctx.project_id),
        }
        if not items and after == 0:
            payload["empty_state"] = dict(views.EMPTY_STATES["activity"])
        return Response(200, payload)

    def stream_events(self, ctx):
        last_event_id = ctx.request.headers.get("last-event-id") or \
            ctx.request.param("last_event_id")
        stream = self.events.frames(ctx.project_id, ctx.principal,
                                    last_event_id=last_event_id)
        return Response(200, stream=stream, content_type="text/event-stream",
                        headers={
                            "Cache-Control": "no-cache, no-transform",
                            "Connection": "keep-alive",
                            # Nothing should buffer an event stream; say so for
                            # any proxy that respects it.
                            "X-Accel-Buffering": "no",
                        })

    # ------------------------------------------------- other lanes' routes

    def _not_this_lane(self, ticket):
        def handler(ctx):
            raise NotFound(
                "This route is published in the contract but not implemented in "
                "this build; {} owns it.".format(ticket),
                {"owner_ticket": ticket},
            )
        return handler

    def messaging_route(self, ctx):
        return self._not_this_lane("T-187")(ctx)

    def runner_route(self, ctx):
        return self._not_this_lane("T-188")(ctx)


# --------------------------------------------------------------- helpers

_CONFIG_CHANGES = [
    {
        "path": "~/.claude/settings.json",
        "change": "add",
        "summary": "Add SessionStart and UserPromptSubmit hook entries owned by "
                   "Ticket Board.",
    },
    {
        "path": "~/.claude/settings.json",
        "change": "none",
        "summary": "Unrelated existing hook entries are preserved.",
    },
]


def views_list_agents(store, project_id):
    rows = store.conn.execute(
        "SELECT * FROM agents WHERE project_id = ? ORDER BY created_at, id",
        (project_id,),
    ).fetchall()
    return [views.serialize_agent(store, store._agent_row(row)) for row in rows]


def _audit_event(row):
    import json as _json

    payload = {
        "id": row["id"],
        "event_id": row["event_id"],
        "project_id": row["project_id"],
        "actor": _json.loads(row["actor"]),
        "action": row["action"],
        "subject_type": row["subject_type"],
        "subject_id": row["subject_id"],
        "request_id": row["request_id"],
        "occurred_at": row["occurred_at"],
        "summary": row["summary"] or "",
    }
    # subject_type/subject_id are plain-typed optionals: omitted when unset,
    # never sent as null. request_id is `anyOf [..., null]` and stays.
    for field in ("subject_type", "subject_id"):
        if payload[field] is None:
            del payload[field]
    return payload


def _satisfies(principal, auth):
    if auth == OPERATOR:
        return principal.is_operator
    if auth == AGENT:
        return principal.is_agent
    return True


def _session_id_taken(session_id):
    """One message for both halves of the duplicate-session guard.

    Callers get the same 400 whether the pre-check saw the row or the insert
    lost the race, so a client cannot tell the two apart and does not need to.
    """
    return MalformedRequest(
        "session_id is already in use; start a new runtime session.",
        {"rejected_fields": ["session_id"]})


def _require_version(subject_id, expected, actual):
    if expected != actual:
        raise TicketVersionConflict(subject_id, expected, actual)


def _version_conflict(key, subject_id, expected, actual):
    """A version conflict on a record that is not a ticket.

    `ticket_version_conflict` is the only version-conflict code the frozen enum
    carries, so an agent-record conflict reuses it and names the real subject in
    `details`. Widening the enum is a contract amendment; misreporting the
    subject would be worse than the borrowed name.
    """
    err = TicketVersionConflict(subject_id, expected, actual)
    err.details = {key: subject_id, "expected_version": expected,
                   "actual_version": actual}
    err.message = "That record was modified by someone else; re-read and retry."
    return err


def _query_bool(raw, name):
    if raw in ("true", "1"):
        return True
    if raw in ("false", "0"):
        return False
    raise MalformedRequest("{} must be true or false.".format(name),
                           {"rejected_fields": [name]})


def _request_id_of(request):
    """The id to echo on an error, from the body if it has one.

    `ErrorResponse.request_id` is a `RequestId`, so anything that is not a UUID
    is left off entirely rather than echoed back into a field the schema
    constrains.
    """
    candidate = request.headers.get("x-request-id")
    if request.method not in ("GET", "HEAD") and request.body:
        try:
            parsed = request.json_body()
            candidate = parsed.get("request_id") or candidate
        except Exception:
            pass
    if isinstance(candidate, str) and ids.REQUEST_ID_RE.match(candidate):
        return candidate
    return None


_ROUTE_TABLE = [
    ("GET",    r"^/overview$", "get_overview", ANY, True),
    ("GET",    r"^/tickets$", "list_tickets", ANY, True),
    ("POST",   r"^/tickets$", "create_ticket", ANY, True),
    ("GET",    r"^/tickets/(?P<ticket_id>[^/]+)$", "get_ticket", ANY, True),
    ("POST",   r"^/tickets/(?P<ticket_id>[^/]+)/claim$", "claim_ticket", AGENT, True),
    ("POST",   r"^/tickets/(?P<ticket_id>[^/]+)/updates$",
     "create_ticket_update", ANY, True),
    ("POST",   r"^/tickets/(?P<ticket_id>[^/]+)/reviews$",
     "request_review", ANY, True),
    ("POST",   r"^/tickets/(?P<ticket_id>[^/]+)/reviews/(?P<review_id>[^/]+)/decision$",
     "decide_review", OPERATOR, True),
    ("POST",   r"^/tickets/(?P<ticket_id>[^/]+)/blocked$",
     "set_ticket_blocked", ANY, True),
    ("GET",    r"^/agents$", "list_agents", ANY, True),
    ("DELETE", r"^/agents/(?P<agent_id>[^/]+)/session-lease$",
     "revoke_session_lease", OPERATOR, True),
    ("POST",   r"^/enrollments$", "create_enrollment", OPERATOR, True),
    ("POST",   r"^/sessions$", "exchange_enrollment", NONE, True),
    ("POST",   r"^/hook-events$", "post_hook_event", AGENT, True),
    ("GET",    r"^/master$", "get_master_panel", ANY, True),
    ("POST",   r"^/master/lease$", "take_master_lease", OPERATOR, True),
    ("POST",   r"^/master/pause$", "set_master_paused", OPERATOR, True),
    ("POST",   r"^/assignments$", "create_assignment", OPERATOR, True),
    ("GET",    r"^/activity$", "list_activity", ANY, True),
    ("GET",    r"^/events$", "stream_events", ANY, True),
    # Declared, not implemented. See `_not_this_lane`.
    ("GET",    r"^/members$", "messaging_route", ANY, True),
    ("GET",    r"^/channels$", "messaging_route", ANY, True),
    ("POST",   r"^/channels$", "messaging_route", ANY, True),
    ("GET",    r"^/messages$", "messaging_route", ANY, True),
    ("POST",   r"^/messages$", "messaging_route", ANY, True),
    ("POST",   r"^/invitations$", "messaging_route", ANY, True),
    ("POST",   r"^/invitations/exchange$", "messaging_route", NONE, True),
    ("POST",   r"^/runners/register$", "runner_route", AGENT, True),
    ("GET",    r"^/runners/jobs$", "runner_route", AGENT, True),
]
