// Copy rules that are contract, not styling — see docs/contracts/dependent-notes.md
// (T-183 section) and docs/interface-v1.md ("State and ownership", "Interface and
// taste"). Keep wording centralized here so no screen invents its own phrasing.

import type {
  AgentState,
  AssignmentState,
  Delivery,
  DeliveryReason,
  DeliveryState,
  HookEventKind,
  MemberAvailability,
  MemberKind,
  TicketState,
} from "./types";

export const ticketStateLabel: Record<TicketState, string> = {
  open: "Ready",
  claimed: "In progress",
  review: "Awaiting review",
  done: "Done",
  blocked: "Blocked",
};

/** 24h completions on a seat — not lifetime done, not median turns. */
export const done24hLabel = "Done(24h)";

export const dayOneLede = "Then intervene when a seat needs you. Objective · Team · Work · Intervene.";

export const DAY_ONE_STEPS = [
  { key: "objective", title: "Objective", body: "name what the team finishes.", cmd: 'tickets objective "…"' },
  { key: "team", title: "Team", body: "register a seat. Coverage by work, not a fixed role.", cmd: "tickets join <you> --roles backend" },
  { key: "work", title: "Work", body: "put a ticket on the board, then claim it.", cmd: 'tickets create "…" · tickets next' },
] as const;

export const dayOneIntervene =
  "Intervene is always available — Msg a seat, route, or unblock. No silent auto-promote.";

// A reservation is never "Working" — it has not been claimed yet.
export const assignmentStateLabel: Record<AssignmentState, string> = {
  queued: "Queued — waiting for agent",
  claimed: "Claimed",
  expired: "Expired",
  withdrawn: "Withdrawn",
};

export const agentStateLabel: Record<AgentState, string> = {
  connected: "Connected",
  idle: "Idle",
  working: "Working",
  "awaiting-input": "Awaiting input",
  offline: "Offline",
  revoked: "Revoked",
};

// `stop` means "Turn finished" — it never implies ticket completion (openapi.yaml,
// HookEvent.kind). `probe` is the synthetic connection test.
export const hookEventKindLabel: Record<HookEventKind, string> = {
  session_start: "Session started",
  user_prompt_submit: "Prompt submitted",
  stop: "Turn finished",
  probe: "Connection test",
};

export function dependencyWaitingLabel(dependencyId: string): string {
  // Never implies work started on the blocked ticket itself.
  return `Waiting on ${dependencyId}`;
}

export function ticketStatusLabel(ticket: { state: TicketState; dependency_blocked: boolean }): string {
  if (ticket.state === "open" && ticket.dependency_blocked) {
    return "Blocked on dependency";
  }
  return ticketStateLabel[ticket.state];
}

export const streamStateCopy = {
  live: (asOf: string) => `Live · updated ${formatTime(asOf)}`,
  reconnecting: (asOf: string) => `Reconnecting… showing updates from ${formatTime(asOf)}`,
  stale: (asOf: string) => `Connection lost. Showing updates from ${formatTime(asOf)}.`,
};

/**
 * Shown when this client has never had a snapshot at all. Distinct from the
 * stale copy above, which promises "updates from <time>" — a promise we cannot
 * keep when there is no time to name.
 */
export const noSnapshotCopy = "Not connected to a board yet.";

/** A replay gap: the cursor aged out and everything on screen was re-read. */
export const recoveredGapCopy = (at: string) =>
  `Reconnected after a gap at ${formatTime(at)} — the board's event history had moved past this tab, so every screen was re-read from the server.`;

export const pendingWriteCopy = "Sending… nothing has changed on the board yet.";

/** The rule that keeps a reservation from reading as work in progress. */
export const queuedNotWorkingNote =
  "Reserved, not started — the agent has not claimed it yet.";

/**
 * Optional overrides for tests. Production call sites omit these so Intl
 * uses the browser/OS local timezone — never a baked-in IANA zone, and
 * never UTC unless that is actually the viewer's zone.
 */
export type FormatTimeOptions = {
  timeZone?: string;
  now?: number;
};

/**
 * Parse a board timestamp. Stored values are UTC; a missing offset must not
 * be read as already-local (that is how a Singapore viewer ends up staring
 * at 14:32 when the clock on the wall says 22:32).
 */
export function parseBoardTime(iso: string): Date | null {
  const raw = String(iso ?? "").trim();
  if (!raw) return null;
  const parseable = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?$/.test(raw)
    ? raw.replace(" ", "T") + "Z"
    : raw;
  const d = new Date(parseable);
  return Number.isNaN(d.getTime()) ? null : d;
}

function localDateOptions(
  extra: Intl.DateTimeFormatOptions,
  timeZone?: string,
): Intl.DateTimeFormatOptions {
  // Omitting `timeZone` is the point: Intl then uses the host local zone.
  return timeZone ? { ...extra, timeZone } : extra;
}

export function formatTime(iso: string, options?: FormatTimeOptions): string {
  const d = parseBoardTime(iso);
  if (!d) return iso;
  return d.toLocaleTimeString(undefined, localDateOptions(
    { hour: "2-digit", minute: "2-digit", timeZoneName: "short" },
    options?.timeZone,
  ));
}

export function formatDateTime(iso: string, options?: FormatTimeOptions): string {
  const d = parseBoardTime(iso);
  if (!d) return iso;
  return d.toLocaleString(undefined, localDateOptions(
    { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short" },
    options?.timeZone,
  ));
}

export function formatRelative(iso: string, options?: FormatTimeOptions): string {
  const d = parseBoardTime(iso);
  if (!d) return iso;
  const now = options?.now ?? Date.now();
  const delta = now - d.getTime();
  const abs = Math.abs(delta);
  const mins = Math.round(abs / 60_000);
  const hours = Math.round(abs / 3_600_000);
  const days = Math.round(abs / 86_400_000);
  if (mins < 1) return "just now";
  const unit = mins < 60 ? `${mins}m` : hours < 48 ? `${hours}h` : `${days}d`;
  return delta >= 0 ? `${unit} ago` : `in ${unit}`;
}

/** Relative + absolute local wall time. The string to put on event rows. */
export function formatWhen(iso: string, options?: FormatTimeOptions): string {
  const absolute = formatDateTime(iso, options);
  if (absolute === iso) return iso;
  return `${formatRelative(iso, options)} · ${absolute}`;
}

export const hookOnlyNote =
  "Hook-only — cannot be woken by a message; a task delivered here shows \"Manual resume required\".";

export const noHeartbeatCopy = (minutes: number) => `No heartbeat for ${minutes}m. Check session.`;

export const connectPreviewNote = "Preview only. This does not install a hook or create credentials.";

// ------------------------------------------------------------------ messages
// docs/contracts/openapi.yaml `Member`, `DeliveryState`, `DeliveryReason` and
// docs/messages-and-runners.md's receipt-chain section.

// Shown in the Members list beside availability and project role.
export const memberKindLabel: Record<MemberKind, string> = {
  human: "Human",
  agent: "Agent",
  master: "Master",
};

export const memberAvailabilityLabel: Record<MemberAvailability, string> = {
  available: "Available",
  busy: "Busy",
  offline: "Offline",
  unknown: "Unknown",
};

// The receipt chain's own step names. A reason-driven state (blocked,
// awaiting_approval, canceled, failed, or a queued with a reason) is never
// shown as a bare state name from this map — `deliveryReceiptLabel` below
// prefers `deliveryReasonLabel` whenever `reason` is set, so "started" can
// only ever appear here, and only when `state` is literally "started".
export const deliveryStateLabel: Record<DeliveryState, string> = {
  sent: "Sent",
  queued: "Queued",
  delivered: "Delivered to runner",
  started: "Agent started",
  responded: "Responded",
  blocked: "Blocked",
  awaiting_approval: "Needs approval",
  canceled: "Canceled",
  failed: "Failed",
};

/**
 * Verbatim-as-possible spec copy for `DeliveryReason` (docs/messages-and-runners.md
 * "Receipt chain" section). `dependency_unmet` reuses `dependencyWaitingLabel`
 * when a `blocking_ticket_id` is present, since that is the same "never imply
 * work started on the blocked ticket" rule the tickets screens already use.
 */
export function deliveryReasonLabel(reason: DeliveryReason, blockingTicketId?: string | null): string {
  switch (reason) {
    case "runner_offline":
      return "Queued — runner offline";
    case "manual_resume_required":
      return "Manual resume required";
    case "agent_busy":
      return "Queued — agent is busy with another claim";
    case "dependency_unmet":
      return blockingTicketId ? dependencyWaitingLabel(blockingTicketId) : "Waiting on a dependency";
    case "permission_required":
      return "Needs approval";
    case "budget_exceeded":
      return "Paused — budget exceeded";
    case "project_paused":
      return "Queued — project paused";
    case "canceled_by_operator":
      return "Canceled by operator";
    case "dispatch_failed":
      return "Failed to dispatch";
  }
}

/**
 * The one line a message's receipt renders. A `reason` always wins over the
 * bare state name — `blocked`, `awaiting_approval`, `queued` and the rest are
 * never shown unexplained when the contract gave us a reason to show instead.
 */
export function deliveryReceiptLabel(delivery: Pick<Delivery, "state" | "reason" | "blocking_ticket_id">): string {
  if (delivery.reason) return deliveryReasonLabel(delivery.reason, delivery.blocking_ticket_id);
  return deliveryStateLabel[delivery.state];
}
