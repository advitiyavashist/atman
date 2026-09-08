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


class RateLimited(BoardError):
    code = "rate_limited"
    status = 429

    def __init__(self, retry_after_seconds):
        super().__init__(
            "Too many hook events. Queue and retry with backoff.",
            {"retry_after_seconds": retry_after_seconds},
        )
