/**
 * TypeScript mirrors of `docs/api/schemas/*.json` (the contract for the
 * `atm ui` JSON API under /api/v1).
 *
 * The schemas are the source of truth. These types are hand-written mirrors so
 * the editor can help; they are not the check. The check is ajv against the
 * real schema files — in dev (`api/devCheck.ts`) and in the tests
 * (`tests/ui/support/schema.ts`), where a missing field fails loudly.
 *
 * Nothing here has a default that could invent data. A value the board never
 * recorded arrives as `null` or `"unknown"` and is rendered that way.
 */

/** common.json#/$defs/harness_badge */
export interface HarnessBadge {
  /** A harness name, "operator" for an operator post, or "unknown". Never defaulted to claude. */
  value: string;
  /** true only when stamped on the post at post time (or an operator post). */
  recorded: boolean;
  note: string;
}

/** common.json#/$defs/receipt — delivery receipts only, never an acknowledgement. */
export interface Receipt {
  agent: string;
  words: string[];
}

/** common.json#/$defs/post */
export interface Post {
  id: string;
  /** ISO-8601 UTC, or "" when not recorded. */
  at: string;
  from: string;
  /** from@project */
  author: string;
  to: string;
  re: string;
  text: string;
  mentions: string[];
  kind: string;
  harness: HarnessBadge;
  operator: boolean;
  broadcast: boolean;
  receipts: Receipt[];
}

/** common.json#/$defs/usage_view — a figure never read is null / "unknown", never 0. */
export interface UsageView {
  provider: string;
  status: string;
  level: string;
  text: string;
  remaining_pct: number | null;
  reset: string;
  checked_at: string;
  /** Always present. "age unknown" when checked_at is "". */
  age: string;
}

/** common.json#/$defs/blocker — the kinds are closed. */
export type BlockerKind =
  | "dep_unaccepted"
  | "dep_open"
  | "seat_limited"
  | "seat_offline"
  | "auth"
  | "hold"
  | "capture"
  | "blocked"
  | "unaccepted";

export interface Blocker {
  kind: BlockerKind;
  on: string;
  text: string;
  /** A copyable atm command, or "". */
  cmd: string;
}

export type CapabilityLine = "takes mid-run messages" | "answers on its next turn";

export interface Capability {
  midrun: boolean;
  line: CapabilityLine;
  reason: string;
}

export interface PickerRow {
  seat: string;
  harness: string;
  capability: CapabilityLine;
}

export interface LeadStatus {
  seat: string;
  harness: string;
  state: string;
  detail: string;
  running: { ticket: string; elapsed_s: number | null; tokens: number | null } | null;
  last_output_at: string;
  last_output_source: string;
  limit: Record<string, unknown> | null;
  limit_until: string;
  auth: { state: string; label: string; cmd: string };
  usage: UsageView;
  reachable: boolean;
  capability: Capability;
  wake_mode: string;
  cannot_answer: { kind: "logged_out" | "limited" | "quota" | "offline"; text: string; cmd: string } | null;
}

/** session.json */
export interface Session {
  api_version: 1;
  token: string;
  token_header: "X-Atman-Token";
  project: string;
  /** "" when no operator is configured: the app is read-only. */
  operator: string;
  operator_note: string;
  lead: string;
  lead_note: string;
}

/** projects.json */
export interface ProjectRow {
  slug: string;
  board: string;
  repos: string[];
  source: "started" | "registry" | "boards";
  counts: Record<string, number>;
  lead: string;
  current: boolean;
}

export interface Projects {
  projects: ProjectRow[];
  current: string;
}

/** board.json (the stable subset) */
export interface AgentRow {
  name: string;
  state: string;
  harness: string;
  lifecycle: string;
  reachable: boolean;
  adapter_state: string;
  wake_mode: string;
  limit: Record<string, unknown> | null;
  limit_until: string;
  auth_surface: { state: string; label: string; recovery?: { cmd?: string } | null };
}

/**
 * One run row of `agent_map.groups[].rows[]`.
 *
 * board.json makes only `agent_map.groups` contract ("other snapshot fields
 * may change"), so every field here is optional and the Runs screen renders
 * "not recorded" rather than a number it cannot prove.
 */
export interface RunRow {
  seat?: string;
  harness?: string;
  ticket?: string;
  title?: string;
  role?: string;
  state?: string;
  /**
   * Not a string on this route: the snapshot records `{kind, sha}` here. It is
   * `unknown` so it can only reach the screen through `verdictChip`.
   */
  verdict?: unknown;
  started?: string;
  ended?: string;
  elapsed_s?: number | null;
  tokens?: number | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
}

export interface RunGroup {
  ticket?: string;
  title?: string;
  status?: string;
  pr?: string;
  rows?: RunRow[];
  runs?: number;
  running?: number;
  elapsed_s?: number | null;
  tokens?: number | null;
  tokens_unknown?: number;
}

export interface Board {
  project: string;
  generated: string;
  master: string;
  cos: string;
  seat?: string;
  counts: { total: number; done: number; done_unverified: number; accepted: number };
  agents: AgentRow[];
  messages: Array<Record<string, unknown>>;
  provider_usage: Array<{ provider: string; level: string; remaining_pct: number | null; text: string }>;
  agent_map: { groups?: RunGroup[] } | null;
  objective: Record<string, unknown>;
  work: { nodes: Array<Record<string, unknown>> } | null;
  app: { project: string; operator: string; operator_note: string; lead: string; lead_note: string };
}

/** plan.json */
export interface PlanNode {
  id: string;
  title: string;
  phase: string;
  status: string;
  status_label: string;
  owner: string;
  reserved_for: string;
  deps: string[];
  depth: number;
  /** done without a structured accept. */
  unverified: boolean;
  /** true only for done with a structured accept or merge record bound to the review head. */
  accepted: boolean;
  /** dependents may start (accept, merge, or a recorded release override). */
  released: boolean;
  blockers: Blocker[];
  running: { seat: string; elapsed_s: number | null } | null;
  review: { verified: boolean; label: string; head?: string };
  artifact: { commit: string; branch: string; pr: string; sha: string };
  wait: Record<string, unknown> | null;
}

export interface Plan {
  project: string;
  generated: string;
  /** false when the Work payload could not be built; nodes is then []. */
  available: boolean;
  objective: Record<string, unknown>;
  summary: Record<string, unknown>;
  counts: Record<string, number>;
  nodes: PlanNode[];
  edges: Array<{ from: string; to: string; waiting?: boolean }>;
  layers: string[][];
  order: string[];
  blocker_kinds: string[];
}

/** thread.json */
export interface Thread {
  project: string;
  operator: string;
  operator_note: string;
  lead: string;
  lead_note: string;
  with: string;
  needs_lead: boolean;
  /** Oldest first. */
  messages: Post[];
  has_more: boolean;
  /** Pass as before= for the previous page. */
  oldest_id: string;
  total_in_window: number;
  archives: boolean;
  error?: string;
}

/** ticket.json */
export interface TicketRun {
  seat: string;
  author: string;
  harness: string;
  role: string;
  state: string;
  verdict: string;
  started: string;
  ended: string;
  elapsed_s: number | null;
  /** null = unknown, never 0. */
  tokens: number | null;
  tokens_in: number | null;
  tokens_out: number | null;
  /** "unknown" whenever tokens is null. */
  tokens_label: string;
}

export interface Verdict {
  kind: string;
  by: string;
  at: string;
  sha: string;
  superseded: boolean;
  /** Bound to the current review head. */
  applies: boolean;
  notes: string;
}

export interface Ticket {
  id: string;
  project: string;
  title: string;
  status: string;
  status_label: string;
  accepted: boolean;
  released: boolean;
  owner: string;
  owner_at_project: string;
  deps: Array<{ id: string; state: string; title: string }>;
  acceptance: { proof: string } & Record<string, unknown>;
  review: { head: string; head_len: number; label: string; verified: boolean; verdicts: Verdict[] };
  runs: TicketRun[];
  usage: UsageView[];
  handoff: Array<{ from: string; by: string; at: string; text: string }>;
  /** Newest first; messages with re=<id>. */
  messages: Post[];
  messages_total: number;
  steers: Array<Record<string, unknown>>;
  artifact: { commit: string; branch: string; pr: string; sha: string };
  diff_cmd: string;
  diff_note: string;
}

/** lead.json */
export interface Lead {
  project: string;
  operator: string;
  operator_note: string;
  lead: string;
  lead_note: string;
  needs_lead: boolean;
  picker: PickerRow[];
  status: LeadStatus | null;
}

/** needs-you.json */
export interface NeedsYouItem {
  kind: "message" | "escalated";
  why: string;
  id: string;
  at: string;
  from: string;
  author: string;
  re: string;
  text: string;
  state: "asked";
  label: "asked (unstructured)";
}

export interface NeedsYou {
  items: NeedsYouItem[];
  count: number;
  operator: string;
  note: string;
}

/** The route name -> schema file map the dev check and the tests share. */
export const SCHEMA_OF = {
  session: "session.json",
  projects: "projects.json",
  board: "board.json",
  plan: "plan.json",
  thread: "thread.json",
  ticket: "ticket.json",
  lead: "lead.json",
  "needs-you": "needs-you.json",
} as const;

export type RouteName = keyof typeof SCHEMA_OF;
