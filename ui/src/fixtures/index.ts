// Typed scenario catalog over the frozen contract pack in tests/fixtures/
// (docs/contracts/manifest.json maps each file to a schema). T-1045 deleted
// the byte-identical copy that used to live under ./data.
//
// SINCE T-184 NO SCREEN READS THESE. Every screen reads the live API through
// BoardClient; these remain only as canned, contract-exact payloads for the
// screen tests in tests/ui/, so a test asserts against a shape the board really
// produces rather than one a test author invented. Nothing here is bundled into
// the app — no application module imports this file.

import type {
  ActivityResponse,
  AgentListResponse,
  MasterPanelResponse,
  OverviewResponse,
  TicketDetailResponse,
  TicketListResponse,
  HookEvent,
} from "../types";

import overviewPopulated from "../../../tests/fixtures/overview/populated.json";
import overviewEmpty from "../../../tests/fixtures/overview/empty.json";
import overviewStale from "../../../tests/fixtures/overview/stale-stream.json";
import overviewBlockedOffline from "../../../tests/fixtures/overview/blocked-and-offline.json";

import ticketsPopulated from "../../../tests/fixtures/tickets/list-populated.json";
import ticketsEmpty from "../../../tests/fixtures/tickets/list-empty.json";
import ticketsDependencyBlocked from "../../../tests/fixtures/tickets/list-dependency-blocked.json";

import ticketDetailClaimed from "../../../tests/fixtures/tickets/detail-claimed.json";
import ticketDetailReviewPending from "../../../tests/fixtures/tickets/detail-review-pending.json";
import ticketDetailAccepted from "../../../tests/fixtures/tickets/detail-accepted.json";
import ticketDetailRejected from "../../../tests/fixtures/tickets/detail-review-rejected.json";
import ticketDetailBlocked from "../../../tests/fixtures/tickets/detail-blocked.json";
import ticketDetailQueuedAssignment from "../../../tests/fixtures/tickets/detail-queued-assignment.json";
import ticketDetailDependencyBlocked from "../../../tests/fixtures/tickets/detail-dependency-blocked.json";
import ticketDetailSuperseded from "../../../tests/fixtures/tickets/detail-superseded-update.json";

import agentsPopulated from "../../../tests/fixtures/agents/list-populated.json";
import agentsEmpty from "../../../tests/fixtures/agents/list-empty.json";
import agentsHookOnly from "../../../tests/fixtures/agents/list-hook-only.json";
import agentsOffline from "../../../tests/fixtures/agents/list-offline.json";
import agentsProbeNotAdopted from "../../../tests/fixtures/agents/list-probe-not-adopted.json";
import agentsRevoked from "../../../tests/fixtures/agents/list-revoked.json";

import activityPopulated from "../../../tests/fixtures/activity/populated.json";
import activityEmpty from "../../../tests/fixtures/activity/empty.json";

import masterActive from "../../../tests/fixtures/master/panel-active.json";
import masterNoEligible from "../../../tests/fixtures/master/panel-no-eligible-agent.json";
import masterPaused from "../../../tests/fixtures/master/panel-paused.json";
import masterUnheld from "../../../tests/fixtures/master/panel-unheld.json";

import hookEventStop from "../../../tests/fixtures/hooks/request-event-stop.json";

export interface Scenario<T> {
  key: string;
  label: string;
  data: T;
}

export const overviewScenarios: Scenario<OverviewResponse>[] = [
  { key: "populated", label: "Populated", data: overviewPopulated as OverviewResponse },
  { key: "empty", label: "Empty board", data: overviewEmpty as OverviewResponse },
  { key: "stale-stream", label: "Stale connection", data: overviewStale as OverviewResponse },
  {
    key: "blocked-and-offline",
    label: "Full attention queue",
    data: overviewBlockedOffline as OverviewResponse,
  },
];

export const ticketListScenarios: Scenario<TicketListResponse>[] = [
  { key: "populated", label: "Populated", data: ticketsPopulated as TicketListResponse },
  { key: "empty", label: "Empty (filtered)", data: ticketsEmpty as TicketListResponse },
  {
    key: "dependency-blocked",
    label: "Dependency-blocked only",
    data: ticketsDependencyBlocked as TicketListResponse,
  },
];

export const ticketDetailScenarios: Scenario<TicketDetailResponse>[] = [
  { key: "claimed", label: "Claimed", data: ticketDetailClaimed as TicketDetailResponse },
  {
    key: "queued-assignment",
    label: "Queued assignment",
    data: ticketDetailQueuedAssignment as TicketDetailResponse,
  },
  {
    key: "dependency-blocked",
    label: "Dependency-blocked",
    data: ticketDetailDependencyBlocked as TicketDetailResponse,
  },
  { key: "review-pending", label: "Awaiting review", data: ticketDetailReviewPending as TicketDetailResponse },
  { key: "accepted", label: "Accepted (done)", data: ticketDetailAccepted as TicketDetailResponse },
  { key: "rejected", label: "Review rejected", data: ticketDetailRejected as TicketDetailResponse },
  { key: "blocked", label: "Blocked", data: ticketDetailBlocked as TicketDetailResponse },
  {
    key: "superseded-update",
    label: "Superseded update",
    data: ticketDetailSuperseded as TicketDetailResponse,
  },
];

export const agentListScenarios: Scenario<AgentListResponse>[] = [
  { key: "populated", label: "Populated", data: agentsPopulated as AgentListResponse },
  { key: "empty", label: "No agents", data: agentsEmpty as AgentListResponse },
  { key: "hook-only", label: "Hook-only session", data: agentsHookOnly as AgentListResponse },
  { key: "offline", label: "Offline", data: agentsOffline as AgentListResponse },
  {
    key: "probe-not-adopted",
    label: "Probe sent, not adopted",
    data: agentsProbeNotAdopted as AgentListResponse,
  },
  { key: "revoked", label: "Lease revoked", data: agentsRevoked as AgentListResponse },
];

export const activityScenarios: Scenario<ActivityResponse>[] = [
  { key: "populated", label: "Populated", data: activityPopulated as ActivityResponse },
  { key: "empty", label: "No activity", data: activityEmpty as ActivityResponse },
];

export const masterPanelScenarios: Scenario<MasterPanelResponse>[] = [
  { key: "active", label: "Active, routing", data: masterActive as MasterPanelResponse },
  {
    key: "no-eligible-agent",
    label: "No eligible agent",
    data: masterNoEligible as MasterPanelResponse,
  },
  { key: "paused", label: "Paused", data: masterPaused as MasterPanelResponse },
  { key: "unheld", label: "Unheld", data: masterUnheld as MasterPanelResponse },
];

// Keyed by agent_id so the Agents screen can show the last hook event next to
// hook_health without inventing one. Only one fixture exists at this freeze.
export const hookEventsByAgentId: Record<string, HookEvent> = {
  [(hookEventStop as { request_id: string; event: HookEvent }).event.agent_id]: (
    hookEventStop as { request_id: string; event: HookEvent }
  ).event,
};
