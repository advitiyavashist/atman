"""Credentials, scope and CSRF.

Two credential types with deliberately different powers, per the freeze:

* **operatorSession** -- an HttpOnly cookie. Unsafe methods additionally need a
  matching `X-CSRF-Token` header and an allowed `Origin`/`Referer`, because a
  cookie is sent by the browser whether or not the page that triggered the
  request is ours.
* **agentToken** -- an opaque bearer token. It can do the work of an agent and
  nothing else: enrollments, review decisions and master authority are 403
  `agent_token_insufficient`.

Every secret is stored as a SHA-256 hash. The raw value exists in the response
that mints it and nowhere else -- not in the database, not in a log line, not
in an error message. That is what makes "the enrollment code is returned
exactly once" true rather than aspirational.

The actor of a mutation is derived here and only here. No route reads an actor
from a request body; a body that carries one is rejected in `validate.py`.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

from ..storage import ids
from .errors import (
    EnrollmentCodeExpired,
    EnrollmentCodeInvalid,
    ForbiddenScope,
    InvitationCodeExpired,
    InvitationCodeInvalid,
    NotFound,
    Unauthenticated,
)

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

# Defaults, all overridable on BoardServer. They are policy, not contract.
OPERATOR_SESSION_SECONDS = 12 * 3600
ENROLLMENT_CODE_SECONDS = 600          # the contract's "expires after 10 minutes"
SESSION_LEASE_SECONDS = 1800

_SECRET_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def now():
    return ids.now()


def in_seconds(seconds, *, at=None):
    """An RFC 3339 UTC timestamp `seconds` from now, in the store's format."""
    base = _dt.datetime.now(_dt.timezone.utc) if at is None else _parse(at)
    return (base + _dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(stamp):
    return _dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=_dt.timezone.utc
    )


def mint_secret(nbytes=24):
    """A URL- and cookie-safe bearer secret."""
    return "".join(secrets.choice(_SECRET_ALPHABET) for _ in range(nbytes))


def hash_secret(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _suffix(n=8):
    return "".join(secrets.choice("0123456789abcdefghijklmnopqrstuvwxyz") for _ in range(n))


def operator_id():
    return "mem_" + _suffix(8)


def enrollment_id():
    return "enr_" + _suffix(8)


def invitation_id():
    return "inv_" + _suffix(8)


class Principal:
    """The authenticated caller, and the actor every audit row will carry."""

    def __init__(self, kind, project_id, actor, *, agent_id=None, session_id=None,
                 operator_id=None, role=None, csrf_hash=None):
        self.kind = kind                  # 'operator' | 'agent'
        self.project_id = project_id
        self.actor = actor
        self.agent_id = agent_id
        self.session_id = session_id
        self.operator_id = operator_id
        self.role = role
        self.csrf_hash = csrf_hash

    @property
    def is_operator(self):
        return self.kind == "operator"

    @property
    def is_agent(self):
        return self.kind == "agent"

    @property
    def id(self):
        return self.agent_id if self.is_agent else self.operator_id

    def as_master(self):
        """The same principal, typed as the master role for a lease record.

        `MasterLease.holder.type` is `master`, not `agent`: the lease is a role,
        and the panel has to be able to say who holds it without implying that
        every action that agent takes is a master action.
        """
        holder = dict(self.actor)
        holder["type"] = "master"
        return holder


class CredentialStore:
    """Reads and writes the server-owned credential tables."""

    def __init__(self, store):
        self.store = store

    @property
    def conn(self):
        return self.store.conn

    # ------------------------------------------------------------- operators

    def create_operator(self, project_id, display_name, *, role="owner"):
        oid = operator_id()
        self.conn.execute(
            "INSERT INTO operators (id, project_id, display_name, role, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (oid, project_id, display_name, role, now()),
        )
        return {"id": oid, "project_id": project_id, "display_name": display_name,
                "role": role}

    def open_operator_session(self, operator, *, ttl_seconds=OPERATOR_SESSION_SECONDS):
        """Mint a dashboard session. Returns the raw secrets exactly once."""
        token = mint_secret(32)
        csrf = mint_secret(24)
        self.conn.execute(
            "INSERT INTO operator_sessions (token_hash, operator_id, project_id,"
            " csrf_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (hash_secret(token), operator["id"], operator["project_id"],
             hash_secret(csrf), now(), in_seconds(ttl_seconds)),
        )
        return {"session_token": token, "csrf_token": csrf,
                "operator_id": operator["id"], "project_id": operator["project_id"]}

    def revoke_operator_session(self, token):
        self.conn.execute(
            "UPDATE operator_sessions SET revoked_at = ? WHERE token_hash = ?",
            (now(), hash_secret(token)),
        )

    # ---------------------------------------------------------- agent tokens

    def issue_agent_token(self, agent_id, project_id, session_id=None):
        token = "tbk_" + mint_secret(32)
        self.conn.execute(
            "INSERT INTO agent_tokens (token_hash, agent_id, project_id, session_id,"
            " created_at) VALUES (?, ?, ?, ?, ?)",
            (hash_secret(token), agent_id, project_id, session_id, now()),
        )
        return token

    def revoke_agent_tokens_for_session(self, session_id):
        """Revoke the credentials minted for one runtime session.

        Lease revocation has to take the token with it. Leaving a live bearer
        token behind after an operator revoked the lease would mean the
        recovery step the contract requires -- explicit, noted, human -- did
        not actually stop the session it was aimed at.
        """
        self.conn.execute(
            "UPDATE agent_tokens SET revoked_at = ?"
            " WHERE session_id = ? AND revoked_at IS NULL",
            (now(), session_id),
        )

    # ----------------------------------------------------------- enrollments

    def create_enrollment(self, project_id, agent_id, *,
                          ttl_seconds=ENROLLMENT_CODE_SECONDS):
        """Mint a single-use code. The raw code is returned once and never stored."""
        code = mint_secret(24)
        eid = enrollment_id()
        self.conn.execute(
            "INSERT INTO enrollments (id, project_id, agent_id, code_hash,"
            " created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (eid, project_id, agent_id, hash_secret(code), now(),
             in_seconds(ttl_seconds)),
        )
        return {"enrollment_id": eid, "code": code,
                "expires_at": in_seconds(ttl_seconds)}

    def consume_enrollment(self, project_id, code):
        """Redeem a code, or raise the indistinguishable-by-message 422 pair.

        Lookup is by hash, so a wrong code costs the same work as a right one
        and no comparison runs over the raw value.
        """
        if not isinstance(code, str) or not code:
            raise EnrollmentCodeInvalid()
        row = self.conn.execute(
            "SELECT * FROM enrollments WHERE code_hash = ? AND project_id = ?",
            (hash_secret(code), project_id),
        ).fetchone()
        if row is None or row["used_at"] is not None:
            # Unknown and already-spent are the same answer on purpose: a
            # second exchange of a used code must not confirm the first.
            raise EnrollmentCodeInvalid()
        if row["expires_at"] <= now():
            raise EnrollmentCodeExpired()
        self.conn.execute(
            "UPDATE enrollments SET used_at = ? WHERE id = ?", (now(), row["id"])
        )
        return {"id": row["id"], "agent_id": row["agent_id"],
                "project_id": row["project_id"]}

    # ----------------------------------------------------------- invitations

    def remember_invitation_code(self, project_id, invitation_id, code):
        """Store the hash of an invite code minted by the record layer.

        T-202 mints and returns a code but has nowhere to keep it, so the
        exchange had nothing to verify. Only the hash is kept, here rather than
        on the record, for the reason in `schema.py`: this is a bearer secret.
        """
        self.conn.execute(
            "INSERT INTO invitation_codes (code_hash, invitation_id, project_id,"
            " created_at) VALUES (?, ?, ?, ?)",
            (hash_secret(code), invitation_id, project_id, now()),
        )

    def consume_invitation_code(self, project_id, code):
        """Redeem an invite code, or raise the indistinguishable 422 pair.

        Unknown, already-spent and belonging-to-another-project are one answer
        on purpose: a stranger with a guessed code must not learn that it exists
        somewhere else, and a second exchange must not confirm the first.
        Expiry is the one distinguishable case, because its holder was given the
        code legitimately and needs to know to ask for a new one.
        """
        if not isinstance(code, str) or not code:
            raise InvitationCodeInvalid()
        row = self.conn.execute(
            "SELECT * FROM invitation_codes WHERE code_hash = ? AND project_id = ?",
            (hash_secret(code), project_id),
        ).fetchone()
        if row is None or row["used_at"] is not None:
            raise InvitationCodeInvalid()
        invitation = self.conn.execute(
            "SELECT * FROM invitations WHERE id = ? AND project_id = ?",
            (row["invitation_id"], project_id),
        ).fetchone()
        if invitation is None or invitation["state"] != "pending":
            raise InvitationCodeInvalid()
        if invitation["expires_at"] <= now():
            raise InvitationCodeExpired()
        return {"invitation_id": row["invitation_id"], "role": invitation["role"],
                "version": invitation["version"]}

    def spend_invitation_code(self, code):
        """Mark the code used. Called inside the exchange's transaction."""
        self.conn.execute(
            "UPDATE invitation_codes SET used_at = ? WHERE code_hash = ?",
            (now(), hash_secret(code)),
        )

    # -------------------------------------------------------- authentication

    def authenticate_all(self, request):
        """Every valid credential on the request, most explicit first.

        A request may legitimately carry both: an operator's browser session and
        an agent token belonging to the same person. OpenAPI `security` is a
        list of *alternatives* -- a route is satisfied if any one of them holds
        -- so resolving a single credential up front and then testing whether it
        happens to fit the route gets it backwards, and refuses a caller who
        did present an acceptable credential. The route picks; this returns the
        candidates.

        A *present but bad* credential is still an error rather than a
        fall-through: otherwise a revoked agent token silently downgrades to
        whatever cookie the browser had, and the caller never learns their token
        died.
        """
        principals = []
        header = request.headers.get("authorization")
        if header:
            scheme, _, value = header.partition(" ")
            if scheme.lower() != "bearer" or not value.strip():
                raise Unauthenticated("Authorization header is not a bearer token.")
            principals.append(self._agent_principal(value.strip()))
        cookie = request.cookies.get("tb_session")
        if cookie:
            principals.append(self._operator_principal(cookie))
        return principals

    def authenticate(self, request):
        """The first valid credential, or None. See `authenticate_all`."""
        principals = self.authenticate_all(request)
        return principals[0] if principals else None

    def _agent_principal(self, token):
        row = self.conn.execute(
            "SELECT * FROM agent_tokens WHERE token_hash = ?", (hash_secret(token),)
        ).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise Unauthenticated()
        try:
            agent = self.store.get_agent(row["agent_id"])
        except NotFound:
            raise Unauthenticated()
        return Principal(
            "agent",
            row["project_id"],
            {
                "type": "agent",
                "id": agent["id"],
                "display_name": agent["name"],
                "session_id": row["session_id"],
            },
            agent_id=agent["id"],
            session_id=row["session_id"],
        )

    def _operator_principal(self, token):
        row = self.conn.execute(
            "SELECT s.*, o.display_name, o.role FROM operator_sessions s"
            " JOIN operators o ON o.id = s.operator_id WHERE s.token_hash = ?",
            (hash_secret(token),),
        ).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise Unauthenticated()
        if row["expires_at"] <= now():
            raise Unauthenticated("Session expired. Sign in again.")
        return Principal(
            "operator",
            row["project_id"],
            {
                "type": "human",
                "id": row["operator_id"],
                "display_name": row["display_name"],
                "session_id": None,
            },
            operator_id=row["operator_id"],
            role=row["role"],
            csrf_hash=row["csrf_hash"],
        )


def in_scope(principal, project_id):
    """Whether this credential is authorized for the requested project.

    The refusal is 403 `forbidden_scope`, never 404 -- frozen, because a 404
    would let anyone with any credential map which project ids exist by
    watching the status code change. The *filtering* lives in `_authorize`,
    which has to consider every credential on the request; this is the single
    predicate both sides agree on.
    """
    return project_id is None or principal.project_id == project_id


def check_csrf(principal, request, *, allowed_origins):
    """Double-submit token plus origin, for cookie-authenticated writes only.

    Agent tokens skip this: a bearer token is not attached by the browser to a
    cross-site request, so there is nothing for an attacker's page to forge.
    """
    if not principal.is_operator or request.method in SAFE_METHODS:
        return
    sent = request.headers.get("x-csrf-token")
    if not sent or not hmac.compare_digest(hash_secret(sent), principal.csrf_hash):
        raise ForbiddenScope("Missing or invalid CSRF token.")
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    if origin:
        candidate = origin
    elif referer:
        split = urlsplit(referer)
        candidate = "{}://{}".format(split.scheme, split.netloc)
    else:
        # A browser sends Origin on every unsafe cross-origin request and on
        # same-origin fetch/XHR. Absent means we cannot tell where this came
        # from, and for a cookie-authenticated write that is a refusal, not a
        # benefit of the doubt.
        raise ForbiddenScope("Unsafe request has no Origin or Referer.")
    if candidate not in allowed_origins:
        raise ForbiddenScope("Origin {} is not allowed.".format(candidate))
