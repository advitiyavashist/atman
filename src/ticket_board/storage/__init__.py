"""Transactional board state (T-179).

Storage for the Ticket Board V1 service, built against the frozen T-178
contract in `docs/contracts/openapi.yaml`. Records serialize to the shapes that
contract publishes, so the API lane (T-180) can return them without reshaping.
"""

from .board import BoardStore
from .errors import (
    BoardError,
    CapacityExhausted,
    DependencyCycle,
    DependencyUnmet,
    ForbiddenScope,
    InvalidReviewEvidence,
    InvalidStateTransition,
    LegacyWriterActive,
    MalformedRequest,
    MembershipRevoked,
    AcceptanceNotEditable,
    MissingAcceptanceCriteria,
    NotChannelMember,
    NotFound,
    RequestIdReused,
    RunAlreadyActive,
    SenderIdentityRejected,
    SessionLeaseExpired,
    TicketAlreadyClaimed,
    TicketVersionConflict,
)

__all__ = [
    "BoardStore",
    "BoardError",
    "CapacityExhausted",
    "DependencyCycle",
    "DependencyUnmet",
    "ForbiddenScope",
    "InvalidReviewEvidence",
    "InvalidStateTransition",
    "LegacyWriterActive",
    "MalformedRequest",
    "MembershipRevoked",
    "AcceptanceNotEditable",
    "MissingAcceptanceCriteria",
    "NotChannelMember",
    "NotFound",
    "RequestIdReused",
    "RunAlreadyActive",
    "SenderIdentityRejected",
    "SessionLeaseExpired",
    "TicketAlreadyClaimed",
    "TicketVersionConflict",
]
