// Request/error/stream shapes for the routes T-184 needs that ../types.ts does
// not already cover (that file only has fixture-read shapes for the T-183
// screens). Mirrors docs/contracts/openapi.yaml at tickets origin/main@884be46
// (post T-178 freeze, post T-183/T-179 merge). No live wire traffic happens in
// this package — see README.md for what "fixture-replay tested" means here.

import type { Agent, Delivery, GitEvidence, MasterLease, Message, SessionLease, Ticket } from "../types";

// ------------------------------------------------------------------ scalars

/** Pattern `^prj_[0-9a-z]{8,32}$` per openapi.yaml; not branded, to match ../types.ts. */
export type ProjectId = string;
export type RequestId = string;

// -------------------------------------------------------------- error shape

export type ErrorCode =
  | "malformed_request"
  | "unauthenticated"
  | "forbidden_scope"
  | "agent_token_insufficient"
  | "not_channel_member"
  | "membership_revoked"
  | "sender_identity_rejected"
  | "not_found"
  | "ticket_version_conflict"
  | "ticket_already_claimed"
  | "capacity_exhausted"
  | "assignment_expired"
  | "session_lease_expired"
  | "master_lease_conflict"
  | "master_lease_expired"
  | "run_already_active"
  | "request_id_reused"
  | "invalid_state_transition"
  | "dependency_cycle"
  | "dependency_unmet"
  | "missing_acceptance_criteria"
  | "invalid_review_evidence"
  | "enrollment_code_invalid"
  | "enrollment_code_expired"
  | "budget_exceeded"
  | "legacy_writer_active"
  | "rate_limited";

/** Codes this client raises itself; never sent by the server. */
export type ClientErrorCode =
  | "client_malformed_request"
  | "unexpected_status"
  | "invalid_response_shape"
  | "network_error";

export interface ErrorEnvelope {
  error: {
    code: ErrorCode;
    status: 400 | 401 | 403 | 404 | 409 | 422 | 429;
    message: string;
    request_id: RequestId | null;
    details?: Record<string, unknown>;
  };
}

// -------------------------------------------------------- mutation envelope

/**
 * Every mutation body carries request_id and nothing that identifies the
 * actor — the credential binds the actor server-side and a body `actor`
 * field is rejected 400 `malformed_request` (frozen decision #1, T-178).
 * There is deliberately no `actor` property anywhere in this file.
 */
export interface MutationEnvelope {
  request_id: RequestId;
}

// ---------------------------------------------------------------- tickets

export interface CreateTicketRequest extends MutationEnvelope {
  title: string;
  outcome: string;
  acceptance: { text: string }[];
  role?: string;
  dependencies?: string[];
  files?: string[];
}

export interface ClaimTicketRequest extends MutationEnvelope {
  expected_version: number;
  session_id: string;
  assignment_id?: string | null;
}

export interface CreateUpdateRequest extends MutationEnvelope {
  body: string;
  next_step: string;
  session_id?: string;
}

export interface CreateReviewRequest extends MutationEnvelope {
  expected_version: number;
  evidence: GitEvidence;
  notes?: string;
}

export interface ReviewDecisionRequest extends MutationEnvelope {
  expected_version: number;
  decision: "accept" | "reject";
  evidence_sha: string;
  notes?: string;
}

export interface SetTicketBlockedRequest extends MutationEnvelope {
  expected_version: number;
  blocked: boolean;
  reason?: string;
}

export interface ListTicketsParams {
  [key: string]: string | number | boolean | undefined;
  cursor?: string;
  limit?: number;
  state?: string;
  role?: string;
  owner?: string;
  dependency_blocked?: boolean;
  q?: string;
}

// ----------------------------------------------------------------- agents

export interface CreateEnrollmentRequest extends MutationEnvelope {
  agent_name: string;
  role: string;
  capabilities?: string[];
  worktree?: string;
  connection_mode?: "managed" | "hook_only";
  max_active_tickets?: number;
}

export interface EnrollmentConfigChange {
  path: string;
  change: "add" | "update" | "none";
  summary?: string;
}

export interface CreateEnrollmentResponse {
  enrollment_id: string;
  agent: Agent;
  expires_at: string;
  /** Real deployments send this once; fixtures carry a placeholder. */
  code: string;
  install_command: string;
  config_changes: EnrollmentConfigChange[];
}

export interface ExchangeEnrollmentRequest extends MutationEnvelope {
  code: string;
  session_id: string;
  runtime?: { adapter: "claude_code"; version?: string };
}

export interface SessionCredentialResponse {
  agent: Agent;
  /** Real deployments send this once; fixtures carry a placeholder. */
  token: string;
  lease: SessionLease;
}

export interface RevokeSessionLeaseRequest extends MutationEnvelope {
  expected_version: number;
  note: string;
}

// ----------------------------------------------------------------- master

export interface MasterLeaseRequest extends MutationEnvelope {
  expected_epoch: number;
  ttl_seconds?: number;
}

export interface MasterPauseRequest extends MutationEnvelope {
  lease_epoch: number;
  paused: boolean;
}

export interface CreateAssignmentRequest extends MutationEnvelope {
  ticket_id: string;
  agent_id: string;
  reason: string;
  lease_epoch: number;
  expected_version: number;
  ttl_seconds?: number;
}

export type { MasterLease, GitEvidence };

// ---------------------------------------------------------------- activity

export interface ListActivityParams {
  [key: string]: string | number | boolean | undefined;
  cursor?: string;
  limit?: number;
  subject_type?: string;
}

// --------------------------------------------------------------- messages

export interface CreateChannelRequest extends MutationEnvelope {
  name: string;
  visibility: "public" | "private";
  topic?: string;
}

export interface AddChannelMemberRequest extends MutationEnvelope {
  member_id: string;
  subscribed?: boolean;
}

export interface ListMessagesParams {
  [key: string]: string | number | boolean | undefined;
  channel_id: string;
  thread_id?: string;
  cursor?: string;
  limit?: number;
}

/**
 * `intent` is deliberately `"message" | "reply"` here, not the full
 * `MessageIntent` union — a task is created through `POST
 * /messages/{id}/task` (see `SendTaskRequest` below), so that its required
 * fields are validated in one place rather than being optional on this body.
 */
export interface SendMessageRequest extends MutationEnvelope {
  channel_id: string;
  body: string;
  intent: "message" | "reply";
  thread_id?: string | null;
  mentions?: string[];
  ticket_id?: string | null;
  causation_id?: string | null;
}

export interface SendMessageResponse {
  message: Message;
  /** Written in the same transaction as the message — a message persisted without its outbox rows is the bug this shape prevents. */
  deliveries: Delivery[];
}

export interface SendTaskRouting {
  mode: "direct" | "via_master";
  /** Required when mode is `direct`. This is an AgentId (`Member.agent_id`), not a MemberId. */
  agent_id?: string | null;
}

export type SendTaskTicketLink = { existing_ticket_id: string } | { new_ticket: { title: string; role?: string } };

/** "Send task" requires an outcome, an assignee or explicit routing, and a linked or new ticket. */
export interface SendTaskRequest extends MutationEnvelope {
  outcome: string;
  routing: SendTaskRouting;
  ticket?: SendTaskTicketLink;
}

export interface SendTaskResponse {
  message: Message;
  ticket: Ticket;
  deliveries: Delivery[];
  /** Null when the recipient is hook-only or the project is paused. */
  wake_job?: unknown | null;
}

// ------------------------------------------------------------------- SSE

export type StreamEventType =
  | "ticket_changed"
  | "agent_changed"
  | "assignment_changed"
  | "master_lease_changed"
  | "review_changed"
  | "message_created"
  | "delivery_changed"
  | "run_changed"
  | "audit_appended"
  | "snapshot_required"
  | "heartbeat";

export type SnapshotRequiredReason = "cursor_expired" | "project_changed" | "acl_changed";

export interface StreamEnvelope {
  event_id: string;
  type: StreamEventType;
  snapshot_version: number;
  occurred_at: string;
  subject_id: string | null;
  payload: Record<string, unknown> | null;
  resume_hint: { reason: SnapshotRequiredReason } | null;
}

export interface SnapshotRequiredEnvelope extends StreamEnvelope {
  type: "snapshot_required";
  resume_hint: { reason: SnapshotRequiredReason };
}
