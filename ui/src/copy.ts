// Copy rules that are contract, not styling — see docs/contracts/dependent-notes.md
// (T-183 section) and docs/interface-v1.md ("State and ownership", "Interface and
// taste"). Keep wording centralized here so no screen invents its own phrasing.

import type { AgentState, AssignmentState, HookEventKind, TicketState } from "./types";

export const ticketStateLabel: Record<TicketState, string> = {
  open: "Ready",
  claimed: "In progress",
  review: "Awaiting review",
  done: "Done",
  blocked: "Blocked",
};

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

export function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const hookOnlyNote =
  "Hook-only — cannot be woken by a message; a task delivered here shows \"Manual resume required\".";

export const noHeartbeatCopy = (minutes: number) => `No heartbeat for ${minutes}m. Check session.`;

export const connectPreviewNote = "Preview only. This does not install a hook or create credentials.";
