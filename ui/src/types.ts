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
  details_url: string | null;
}

export interface GitEvidence {
  repository?: string;
  branch: string;
  sha: string;
  pr_url: string | null;
  checks: GitEvidenceCheck[];
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
