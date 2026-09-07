"""API-layer errors, continuing the storage hierarchy.

T-179 already raises contract-shaped errors for everything the store can decide
(version conflicts, capacity, cycles, idempotency). The codes below are the
ones only the API layer can raise -- they are about credentials, scope, master
authority and rate limits, none of which the store knows about.

They subclass `storage.BoardError` on purpose: one `except BoardError` in the
router renders every failure, so there is exactly one place that turns an
exception into an `ErrorResponse` and no chance of two mappings drifting apart.
"""

from __future__ import annotations

from ..storage.errors import (  # noqa: F401  (re-exported for the router)
    BoardError,
    CapacityExhausted,
    DependencyCycle,
    DependencyUnmet,
    InvalidReviewEvidence,
    InvalidStateTransition,
    LegacyWriterActive,
    MalformedRequest,
    MissingAcceptanceCriteria,
    NotFound,
    RequestIdReused,
    RunAlreadyActive,
    SessionLeaseExpired,
    TicketAlreadyClaimed,
    TicketVersionConflict,
)


class Unauthenticated(BoardError):
    code = "unauthenticated"
    status = 401

    def __init__(self, message="No valid credential on this request.", details=None):
        super().__init__(message, details)


class ForbiddenScope(BoardError):
    code = "forbidden_scope"
    status = 403

    def __init__(self,
                 message="This credential is not authorized for the requested project.",
                 details=None):
        super().__init__(message, details)


class AgentTokenInsufficient(BoardError):
    code = "agent_token_insufficient"
    status = 403

    def __init__(self, route):
        super().__init__(
            "Agent tokens cannot create enrollments, accept reviews or take "
            "master authority.",
            {"route": route},
        )


class MasterLeaseConflict(BoardError):
    code = "master_lease_conflict"
    status = 409

    def __init__(self, expected_epoch, actual_epoch):
        super().__init__(
            "Another master already took the lease at epoch {}.".format(actual_epoch),
            {"expected_epoch": expected_epoch, "actual_epoch": actual_epoch},
        )


class MasterLeaseExpired(BoardError):
    code = "master_lease_expired"
    status = 409

    def __init__(self, sent_epoch, current_epoch):
        super().__init__(
            "Your master lease was superseded. Routing mutations at epoch {} "
            "are rejected.".format(sent_epoch),
            {"sent_epoch": sent_epoch, "current_epoch": current_epoch},
        )


class AssignmentExpired(BoardError):
    code = "assignment_expired"
    status = 409

    def __init__(self, assignment_id, expires_at):
        super().__init__(
            "This reservation expired at {} and can no longer be claimed.".format(
                expires_at[11:16] if len(expires_at) >= 16 else expires_at
            ),
            {"assignment_id": assignment_id, "expires_at": expires_at},
        )


# The enrollment pair below deliberately share one message. The contract asks
# for that so a caller cannot probe a code for partial correctness by reading
# the prose. Be honest about the residual: the *code* still differs, so a
# caller who submits a real-but-expired code can tell it was real. Narrowing
# that would mean collapsing two frozen error codes into one, which is a
# contract amendment, not a route-layer decision -- it is raised in
# docs/api-notes.md rather than fixed here.
_ENROLLMENT_MESSAGE = "That enrollment code is not usable."


class EnrollmentCodeInvalid(BoardError):
    code = "enrollment_code_invalid"
    status = 422

    def __init__(self, details=None):
        super().__init__(_ENROLLMENT_MESSAGE, details)


class EnrollmentCodeExpired(BoardError):
    code = "enrollment_code_expired"
    status = 422

    def __init__(self, details=None):
        super().__init__(_ENROLLMENT_MESSAGE, details)


_INVITATION_MESSAGE = "That invitation code is not usable."


class InvitationCodeInvalid(EnrollmentCodeInvalid):
    """A bad invite code, reported under the enrollment code's frozen name.

    `ErrorCode` is a closed enum and has no `invitation_code_*` member, but the
    contract's `POST /invitations/exchange` publishes a 422. Widening the enum
    would be a unilateral amendment to a frozen contract, and answering with
    `malformed_request` would misreport a *credential* failure as a shape
    failure. So the code is borrowed and the message names the real subject --
    the same trade T-180 made for `ticket_version_conflict` on agent records.
    Recorded for T-224's amendment list; see docs/api-notes.md.
    """

    def __init__(self, details=None):
        BoardError.__init__(self, _INVITATION_MESSAGE, details)


class InvitationCodeExpired(EnrollmentCodeExpired):
    """An expired invite code. Borrowed name; see `InvitationCodeInvalid`."""

    def __init__(self, details=None):
        BoardError.__init__(self, _INVITATION_MESSAGE, details)


class InvitationReplayRefused(RequestIdReused):
    """A replay of `POST /invitations` that cannot be re-answered honestly.

    `ErrorCode` has no member for "this one-time secret was already issued
    and cannot be reissued", so the code is borrowed the same way
    `InvitationCodeInvalid` borrows `enrollment_code_invalid` -- widening the
    enum is a contract amendment, not a route-layer decision. Recorded for
    T-224's amendment list; see docs/api-notes.md.

    T-286: `store.create_invitation`'s idempotency record cannot hold the real
    code (only its hash is kept, in `credentials`), so a byte-identical replay
    of `request_id` has nothing honest to return -- the stored placeholder
    was never registered and 422s if redeemed. Refusing the replay outright
    keeps the invite code a write-once bearer secret that is never persisted
    in plaintext in the replay log; the alternative (passing the API's minted
    code into the store so replay could echo it) was rejected for exactly
    that reason in T-284's review of this defect.
    """

    def __init__(self, request_id):
        BoardError.__init__(
            self,
            "This request_id already issued a one-time invitation code; it "
            "cannot be replayed. Issue a new invitation instead.",
            {"request_id": request_id},
        )


class RateLimited(BoardError):
    code = "rate_limited"
    status = 429

    def __init__(self, retry_after_seconds):
        super().__init__(
            "Too many hook events. Queue and retry with backoff.",
            {"retry_after_seconds": retry_after_seconds},
        )
