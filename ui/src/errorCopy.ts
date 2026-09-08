import { BoardError } from "./api";
import type { ClientErrorCode, ErrorCode } from "./api";

/**
 * What an operator should do about each failure, in their words.
 *
 * The server's `message` is accurate but written for a client author. The
 * dashboard's job is to say what happened *to this operator's action* and what
 * would fix it, so this maps the frozen error codes to an action rather than
 * re-printing the wire message. `detail` still carries the server's message
 * underneath, because when the guess here is wrong the raw text is what makes
 * that discoverable.
 *
 * A code with no entry falls through to the server's own message. That is the
 * right default: inventing friendly copy for a code we have not thought about
 * would state something we do not know.
 */

export interface ErrorPresentation {
  /** One line, imperative where there is something to do. */
  headline: string;
  /** The server's own message, always shown so nothing is hidden. */
  detail: string;
  /** True when retrying the identical request could plausibly work. */
  retryable: boolean;
  /** True when the screen's snapshot is known to be behind and must be re-read. */
  needsReload: boolean;
}

const HEADLINES: Partial<Record<ErrorCode | ClientErrorCode, string>> = {
  forbidden_scope:
    "Refused — this ticket now belongs to another agent. Nothing was recorded.",
  agent_token_insufficient:
    "Refused — this action needs the operator session, not an agent token.",
  unauthenticated: "Your board session is not valid. Reload to sign in again.",
  ticket_version_conflict:
    "Someone changed this ticket while you were looking at it. Reload and try again.",
  ticket_already_claimed: "Another agent claimed this ticket first.",
  capacity_exhausted: "That agent is already at its ticket limit.",
  assignment_expired: "The reservation expired before it was claimed.",
  session_lease_expired:
    "That agent's session lease is gone. It has to reconnect before it can claim.",
  master_lease_conflict:
    "Another master took the lease first. Reload the panel to see who holds it.",
  master_lease_expired: "The master lease expired. Take it again before routing.",
  request_id_reused:
    "The board already handled a different request under this id. Reload before retrying.",
  invalid_state_transition: "That move is not allowed from this ticket's state.",
  dependency_cycle: "Those dependencies would form a cycle. Nothing was created.",
  dependency_unmet: "This ticket is still waiting on a dependency.",
  missing_acceptance_criteria:
    "A ticket needs an outcome and at least one acceptance criterion.",
  invalid_review_evidence:
    "The evidence does not match the review that was submitted, or a check has failed.",
  enrollment_code_invalid: "That enrollment code has already been used or is not valid.",
  enrollment_code_expired: "That enrollment code expired. Issue a new one.",
  not_channel_member: "You are not a member of this channel. Ask an owner or admin to add you.",
  membership_revoked: "Your membership in this project was revoked.",
  sender_identity_rejected:
    "The board rejected this message's sender field. The composer never sends one — reload and retry.",
  rate_limited: "The board is rate-limiting this client. Wait a moment, then retry.",
  not_found: "The board has no such record.",
  malformed_request: "The board rejected the request as malformed.",
  network_error: "The board did not answer. Check that the server is still running.",
  unexpected_status: "The board answered in a way this dashboard does not recognise.",
  invalid_response_shape:
    "The board's answer did not match the contract, so it was not displayed.",
  client_malformed_request: "This dashboard built an invalid request and did not send it.",
};

/** Retrying these unchanged can succeed; everything else needs a decision first. */
const RETRYABLE = new Set<ErrorCode | ClientErrorCode>(["rate_limited", "network_error"]);

/** These mean the snapshot on screen is provably behind the board. */
const NEEDS_RELOAD = new Set<ErrorCode | ClientErrorCode>([
  "ticket_version_conflict",
  "ticket_already_claimed",
  "master_lease_conflict",
  "master_lease_expired",
  "request_id_reused",
  "forbidden_scope",
  "assignment_expired",
]);

export function presentError(error: BoardError): ErrorPresentation {
  return {
    headline: HEADLINES[error.code] ?? error.message,
    detail: error.message,
    retryable: RETRYABLE.has(error.code),
    needsReload: NEEDS_RELOAD.has(error.code),
  };
}

/**
 * The 409 conflict copy, spelled out with both versions.
 *
 * The contract puts `expected_version` and `actual_version` in the body
 * specifically so one re-read settles it (docs/storage-notes.md #3). Showing
 * the numbers is not decoration — it is how an operator can tell "I am one
 * behind" from "I am looking at something much older".
 */
export function versionConflictDetail(error: BoardError): string | null {
  const conflict = error.versionConflict();
  if (!conflict) return null;
  const behind = conflict.actual_version - conflict.expected_version;
  return `You were acting on version ${conflict.expected_version}; the board is at ${conflict.actual_version}` +
    (behind === 1 ? " — one change behind." : ` — ${behind} changes behind.`);
}

/**
 * Copy for a write the board accepted from a session that no longer owns the
 * ticket. It is recorded and kept, and it changed nothing — both halves matter,
 * and calling it either "saved" or "failed" alone would be a lie.
 */
export const supersededNote =
  "Recorded, but this session no longer owns the ticket — the update is kept in the trail and did not change ticket state.";
