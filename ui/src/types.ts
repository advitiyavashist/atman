// Mirrors component schemas in ../../docs/contracts/openapi.yaml (frozen at
// opus-backend/e010-contract@855e15a). This UI is fixture-only (T-183): these
// types describe fixture shapes, not a live wire client.

export type ActorType = "human" | "agent" | "master" | "system";

export interface Actor {
  type: ActorType;
  id: string;
  display_name: string;
  session_id: string | null;
}

export interface StreamStatus {
  state: "live" | "reconnecting" | "stale";
  snapshot_version: number;
  as_of: string;
  last_event_id: string | null;
}

export interface EmptyState {
  headline: string;
  detail: string | null;
  primary_action: string | null;
}

export interface GitEvidenceCheck {
  name: string;
  status: "passed" | "failed" | "pending" | "skipped";
  /**
   * Optional in the contract (`GitEvidence.checks[].required` is `[name,
   * status]`), and the board really does omit it. Typing it as required made
   * every consumer believe a property that may not be there.
   */
  details_url?: string | null;
}

/**
 * `GitEvidence.required` is exactly `[branch, sha]`. Every other member is
 * optional, and the board omits the ones it has no value for rather than
 * sending nulls — so this interface marks optional what the contract marks
 * optional, no more and no less.
 */
export interface GitEvidence {
  repository?: string;
  branch: string;
  sha: string;
  pr_url?: string | null;
  /**
   * Optional: `GitEvidence.required` is `[branch, sha]`, so a review may be
   * submitted with no checks at all and the board echoes the evidence back
   * with no `checks` key. Typed as required until T-184, which meant
   * `evidence.checks.map(...)` — in both the ticket-evidence block and the
   * review-decision panel — threw on a contract-legal review. Verified against
   * a live board, not inferred from the schema.
   */
  checks?: GitEvidenceCheck[];
}

export type TicketState = "open" | "claimed" | "review" | "done" | "blocked";

export interface TicketAcceptanceItem {
  text: string;
  checked: boolean;
  checked_by: Actor | null;
  checked_at: string | null;
}

export interface Ticket {
  id: string;
  project_id: string;
  title: string;
  outcome?: string;
  acceptance?: TicketAcceptanceItem[];
  state: TicketState;
  version: number;
  role?: string;
  owner: string | null;
  owner_session: string | null;
  dependencies: string[];
  dependency_blocked: boolean;
  blocked_reason: string | null;
  files?: string[];
  worktree: string | null;
  evidence: GitEvidence | null;
  next_step: string | null;
  handoff: string | null;
  created_at: string;
  updated_at: string;
  claimed_at: string | null;
  last_progress_at: string | null;
}

export interface TicketUpdate {
  id: string;
  ticket_id: string;
  author: Actor;
  body: string;
  next_step: string | null;
  created_at: string;
  superseded: boolean;
}

export interface Review {
  id: string;
  ticket_id: string;
  state: "requested" | "accepted" | "rejected";
  submitted_by: Actor;
  submitted_at: string;
  evidence: GitEvidence;
  notes?: string;
  decided_by: Actor | null;
  decided_at: string | null;
  decision_notes: string | null;
}

export type AssignmentState = "queued" | "claimed" | "expired" | "withdrawn";

export interface Assignment {
  id: string;
  ticket_id: string;
  agent_id: string;
  state: AssignmentState;
  reason: string;
  created_at: string;
  expires_at: string;
  lease_epoch: number;
  version: number;
}

export interface DependencySummary {
  id: string;
  title: string;
  state: TicketState;
}

export type TicketAction =
  | "assign"
  | "claim"
  | "update"
  | "request_review"
  | "accept"
  | "reject"
  | "block"
  | "unblock";

export interface TicketListResponse {
  items: Ticket[];
  next_cursor: string | null;
  total_matching?: number;
  stream: StreamStatus;
  empty_state?: EmptyState;
}

export interface TicketDetailResponse {
  ticket: Ticket;
  updates: TicketUpdate[];
  reviews: Review[];
  assignment: Assignment | null;
  dependencies: DependencySummary[];
  available_actions: TicketAction[];
  stream: StreamStatus;
}

export type AgentState =
  | "connected"
  | "idle"
  | "working"
  | "awaiting-input"
  | "offline"
  | "revoked";

export interface HookHealth {
  config_installed: boolean;
  server_received: boolean;
  response_delivered: boolean;
  session_adopted: boolean;
  last_error: string | null;
}

export interface SessionLease {
  session_id: string;
  agent_id: string;
  acquired_at: string;
  expires_at: string;
  version: number;
  revoked_at: string | null;
  revocation_note: string | null;
}

export interface Agent {
  id: string;
  project_id: string;
  name: string;
  role: string;
  capabilities: string[];
  state: AgentState;
  connection_mode: "managed" | "hook_only";
  runtime: {
    adapter: "claude_code";
    version?: string;
    unsupported_capabilities: string[];
  };
  capacity: { max_active_tickets: number; active_tickets: number };
  current_ticket: string | null;
  session: SessionLease | null;
  worktree: string | null;
  hook_health: HookHealth;
  last_heartbeat_at: string | null;
  last_progress_at: string | null;
  version: number;
  created_at: string;
}

export interface AgentListResponse {
  items: Agent[];
  stream: StreamStatus;
  empty_state?: EmptyState;
}

export interface MasterLease {
  holder: Actor | null;
  epoch: number;
  acquired_at: string;
  expires_at: string;
  routing_mode: "deterministic" | "deterministic_plus_suggestions";
  paused: boolean;
  last_sweep_at: string | null;
  next_sweep_at: string | null;
  sweep_interval_seconds: number;
}

export type RoutingDecisionKind =
  | "assigned"
  | "skipped"
  | "split_recommended"
  | "no_eligible_agent";

export interface RoutingDecision {
  ticket_id: string;
  decision: RoutingDecisionKind;
  reason: string;
  agent_id: string | null;
  decided_at: string;
}

export interface MasterPanelResponse {
  lease: MasterLease;
  queue: Assignment[];
  decisions: RoutingDecision[];
  stream: StreamStatus;
}

export type AttentionKind =
  | "awaiting_review"
  | "no_heartbeat"
  | "progress_stalled"
  | "hook_delivery_failed"
  | "runner_offline"
  | "needs_approval"
  | "master_lease_expired"
  | "blocked";

export interface AttentionItem {
  kind: AttentionKind;
  subject_type: "ticket" | "agent" | "run" | "master_lease";
  subject_id: string;
  headline: string;
  raised_at: string;
  acknowledged_by: Actor | null;
}

export interface OverviewCounts {
  ready: number;
  in_progress: number;
  awaiting_review: number;
  blocked: number;
  dependency_blocked: number;
}

export interface Project {
  id: string;
  name: string;
  version: number;
  created_at: string;
  source: "native" | "imported_legacy";
  paused: boolean;
}

export interface OverviewResponse {
  project: Project;
  counts: OverviewCounts;
  attention: AttentionItem[];
  recent_accepted: Review[];
  master: MasterLease;
  stream: StreamStatus;
  empty_state?: EmptyState;
}

export type AuditSubjectType =
  | "ticket"
  | "agent"
  | "assignment"
  | "master_lease"
  | "review"
  | "message"
  | "run"
  | "member"
  | "channel"
  | "project";

export interface AuditEvent {
  id: string;
  event_id: string;
  project_id: string;
  actor: Actor;
  action: string;
  subject_type: AuditSubjectType;
  subject_id: string;
  request_id: string | null;
  occurred_at: string;
  summary: string;
}

export interface ActivityResponse {
  items: AuditEvent[];
  next_cursor: string | null;
  stream: StreamStatus;
  empty_state?: EmptyState;
}

export type HookEventKind = "session_start" | "user_prompt_submit" | "stop" | "probe";

export interface HookEvent {
  event_id: string;
  agent_id: string;
  session_id: string;
  kind: HookEventKind;
  occurred_at: string;
  cwd: string | null;
  note: string | null;
}

// ---------------------------------------------------------------- messages
//
// T-187's read shapes (docs/contracts/openapi.yaml, "Scope amendment,
// docs/messages-and-runners.md"). Built for T-189 against the frozen
// contract — no route here has ever been called against a live messaging
// server; wiring one up is a later ticket.

export type ProjectRole = "owner" | "admin" | "member" | "viewer";

export type MemberKind = "human" | "agent" | "master";

export type MemberState = "active" | "revoked";

export type MemberAvailability = "available" | "busy" | "offline" | "unknown";

export interface Member {
  id: string;
  project_id: string;
  kind: MemberKind;
  display_name: string;
  role: ProjectRole;
  /** Revocation cancels pending unauthorized deliveries; it does not delete artifacts already produced. */
  state: MemberState;
  /** Set when `kind` is agent or master. */
  agent_id: string | null;
  availability: MemberAvailability;
  version: number;
  created_at: string;
}

export type ChannelKind = "channel" | "dm";

export type ChannelVisibility = "public" | "private";

export interface Channel {
  id: string;
  project_id: string;
  name: string;
  kind: ChannelKind;
  /** Private channels and DMs require explicit membership. */
  visibility: ChannelVisibility;
  topic: string | null;
  /** Unaddressed task posts here wake this master to decide the owner, rather than waking every subscribed agent. */
  designated_master: string | null;
  unread_count: number;
  version: number;
  created_at: string;
}

export interface ChannelMember {
  channel_id: string;
  member_id: string;
  /** Controls ordinary conversation delivery only; a mention or an addressed task is what wakes an agent. */
  subscribed: boolean;
  joined_at: string;
}

export interface Thread {
  id: string;
  channel_id: string;
  root_message_id: string;
  ticket_id: string | null;
  reply_count: number;
  updated_at: string;
}

/**
 * `task` is created through `POST /messages/{id}/task`, never sent directly
 * as `intent: "task"` in `SendMessageRequest` (see `SendTaskRequest` in
 * ./api/types.ts). `receipt` messages are system-authored and never wake
 * their author or generate another auto-reply.
 */
export type MessageIntent = "message" | "task" | "reply" | "receipt";

export interface Message {
  id: string;
  project_id: string;
  channel_id: string;
  thread_id: string | null;
  /** Derived from the credential. A body that tries to set an author is rejected 403 `sender_identity_rejected`. */
  author: Actor;
  body: string;
  intent: MessageIntent;
  ticket_id: string | null;
  mentions: string[];
  causation_id: string | null;
  conversation_id: string | null;
  supersedes_message_id: string | null;
  version: number;
  created_at: string;
}

/**
 * Receipt chain: sent -> queued -> delivered -> started -> responded, with
 * blocked / awaiting_approval / canceled / failed as visible alternatives.
 * `started` requires runtime session initialization AND, for an execution
 * request, a valid task claim — delivery is not acknowledgement and a process
 * spawn is not proof of execution. No screen may render "started" unless
 * `state` is literally `"started"`.
 */
export type DeliveryState =
  | "sent"
  | "queued"
  | "delivered"
  | "started"
  | "responded"
  | "blocked"
  | "awaiting_approval"
  | "canceled"
  | "failed";

/** Displayed verbatim as the reason a delivery is not progressing (see ./copy.ts `deliveryReasonLabel`). */
export type DeliveryReason =
  | "runner_offline"
  | "manual_resume_required"
  | "agent_busy"
  | "dependency_unmet"
  | "permission_required"
  | "budget_exceeded"
  | "project_paused"
  | "canceled_by_operator"
  | "dispatch_failed";

export interface Delivery {
  id: string;
  message_id: string;
  recipient_agent_id: string;
  state: DeliveryState;
  reason: DeliveryReason | null;
  reason_detail: string | null;
  run_id: string | null;
  blocking_ticket_id: string | null;
  attempts: number;
  next_attempt_at: string | null;
  created_at: string;
  updated_at?: string;
  version: number;
}

export interface MemberListResponse {
  items: Member[];
}

export interface ChannelListResponse {
  items: Channel[];
  stream: StreamStatus;
  empty_state?: EmptyState;
}

export interface MessageListResponse {
  items: Message[];
  /** Present so the pane can show reply-count and ticket chips in the same read as the messages. */
  threads?: Thread[];
  /** Receipts for the returned messages, so the pane renders in one read. */
  deliveries?: Delivery[];
  next_cursor: string | null;
  stream: StreamStatus;
  empty_state?: EmptyState;
}

export interface DeliveryListResponse {
  items: Delivery[];
  /**
   * Runs each delivery started. Untyped here (`Run` is out of this ticket's
   * acceptance line — see docs/contracts/openapi.yaml `Run` if a later ticket
   * needs to render it); the screen never reads into this array's shape.
   */
  runs?: unknown[];
}
