"""Storage errors, bound to the frozen T-178 error codes.

Every error the storage layer raises maps to one `ErrorCode` in
`docs/contracts/openapi.yaml` and carries the `details` the contract fixture
for that code publishes. The API lane (T-180) renders these; it must not have
to invent a code or a status, and it must not have to guess what goes in
`details` -- a version conflict that does not carry `expected_version` and
`actual_version` forces the caller into a second round trip, which the
contract explicitly rules out.
"""


class BoardError(Exception):
    """Base for every storage failure that maps to a contract error code."""

    code = "malformed_request"
    status = 400

    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_error_response(self, request_id=None):
        """Render as the contract's ErrorResponse shape.

        Matches `tests/fixtures/errors/*.json`, so the API lane can return this
        straight out without reshaping it.
        """
        error = {"code": self.code, "status": self.status, "message": self.message}
        if request_id is not None:
            error["request_id"] = request_id
        if self.details:
            error["details"] = self.details
        return {"error": error}


class MalformedRequest(BoardError):
    code = "malformed_request"
    status = 400


class NotFound(BoardError):
    code = "not_found"
    status = 404


class TicketVersionConflict(BoardError):
    code = "ticket_version_conflict"
    status = 409

    def __init__(self, ticket_id, expected_version, actual_version):
        super().__init__(
            "Ticket was modified by someone else; re-read and retry.",
            {
                "ticket_id": ticket_id,
                "expected_version": expected_version,
                "actual_version": actual_version,
            },
        )


class TicketAlreadyClaimed(BoardError):
    code = "ticket_already_claimed"
    status = 409

    def __init__(self, ticket_id, owner):
        super().__init__(
            "Ticket is already claimed.",
            {"ticket_id": ticket_id, "owner": owner},
        )


class CapacityExhausted(BoardError):
    code = "capacity_exhausted"
    status = 409

    def __init__(self, agent_id, active_tickets, max_active_tickets):
        super().__init__(
            "Agent is at its active-ticket capacity.",
            {
                "agent_id": agent_id,
                "active_tickets": active_tickets,
                "max_active_tickets": max_active_tickets,
            },
        )


class SessionLeaseExpired(BoardError):
    code = "session_lease_expired"
    status = 409

    def __init__(self, session_id, detail=None):
        super().__init__(
            "Session lease is expired or revoked.",
            {"session_id": session_id, **(detail or {})},
        )


class RequestIdReused(BoardError):
    code = "request_id_reused"
    status = 409

    def __init__(self, request_id):
        super().__init__(
            "This request_id was already used with a different body.",
            {"request_id": request_id},
        )


class LegacyWriterActive(BoardError):
    code = "legacy_writer_active"
    status = 409

    def __init__(self, path, owner=None):
        super().__init__(
            "This board is owned by the server; direct legacy writes are refused.",
            {"path": str(path), "owner": owner},
        )


class InvalidStateTransition(BoardError):
    code = "invalid_state_transition"
    status = 422

    def __init__(self, ticket_id, from_state, to_state):
        super().__init__(
            "That ticket state transition is not allowed.",
            {"ticket_id": ticket_id, "from": from_state, "to": to_state},
        )


class DependencyCycle(BoardError):
    code = "dependency_cycle"
    status = 422

    def __init__(self, cycle):
        super().__init__(
            "Those dependencies would form a cycle.",
            {"cycle": list(cycle)},
        )


class DependencyUnmet(BoardError):
    code = "dependency_unmet"
    status = 422

    def __init__(self, ticket_id, unmet):
        super().__init__(
            "Ticket has dependencies that are not done.",
            {"ticket_id": ticket_id, "unmet": list(unmet)},
        )


class MissingAcceptanceCriteria(BoardError):
    code = "missing_acceptance_criteria"
    status = 422

    def __init__(self, ticket_id):
        super().__init__(
            "Ticket needs acceptance criteria before it can go to review.",
            {"ticket_id": ticket_id},
        )


class InvalidReviewEvidence(BoardError):
    code = "invalid_review_evidence"
    status = 422

    def __init__(self, message, details=None):
        super().__init__(message, details)
