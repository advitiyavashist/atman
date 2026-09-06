// Fixture loader. Files under ./data are a verbatim copy of the frozen contract's
// tests/fixtures/ (opus-backend/e010-contract@855e15a, docs/contracts/manifest.json
// is the source of truth for what validates against what). T-183 builds against
// these alone — no live API call happens anywhere in this package; that is T-184.

import type {
  ActivityResponse,
  AgentListResponse,
  MasterPanelResponse,
  OverviewResponse,
  TicketDetailResponse,
  TicketListResponse,
  HookEvent,
} from "../types";

import overviewPopulated from "./data/overview/populated.json";
import overviewEmpty from "./data/overview/empty.json";
import overviewStale from "./data/overview/stale-stream.json";
import overviewBlockedOffline from "./data/overview/blocked-and-offline.json";

import ticketsPopulated from "./data/tickets/list-populated.json";
import ticketsEmpty from "./data/tickets/list-empty.json";
import ticketsDependencyBlocked from "./data/tickets/list-dependency-blocked.json";

import ticketDetailClaimed from "./data/tickets/detail-claimed.json";
import ticketDetailReviewPending from "./data/tickets/detail-review-pending.json";
import ticketDetailAccepted from "./data/tickets/detail-accepted.json";
import ticketDetailRejected from "./data/tickets/detail-review-rejected.json";
import ticketDetailBlocked from "./data/tickets/detail-blocked.json";
import ticketDetailQueuedAssignment from "./data/tickets/detail-queued-assignment.json";
import ticketDetailDependencyBlocked from "./data/tickets/detail-dependency-blocked.json";
import ticketDetailSuperseded from "./data/tickets/detail-superseded-update.json";

import agentsPopulated from "./data/agents/list-populated.json";
import agentsEmpty from "./data/agents/list-empty.json";
import agentsHookOnly from "./data/agents/list-hook-only.json";
import agentsOffline from "./data/agents/list-offline.json";
import agentsProbeNotAdopted from "./data/agents/list-probe-not-adopted.json";
import agentsRevoked from "./data/agents/list-revoked.json";

import activityPopulated from "./data/activity/populated.json";
import activityEmpty from "./data/activity/empty.json";

import masterActive from "./data/master/panel-active.json";
import masterNoEligible from "./data/master/panel-no-eligible-agent.json";
import masterPaused from "./data/master/panel-paused.json";
import masterUnheld from "./data/master/panel-unheld.json";

import hookEventStop from "./data/hooks/request-event-stop.json";

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
