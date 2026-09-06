import type { Agent, AgentListResponse, Assignment, ActivityResponse, MasterPanelResponse, OverviewResponse, Review, Ticket, TicketDetailResponse, TicketListResponse, TicketUpdate } from "../types";
import { boardRequest, type BoardClientConfig } from "./http";
import type {
  ClaimTicketRequest,
  CreateAssignmentRequest,
  CreateEnrollmentRequest,
  CreateEnrollmentResponse,
  CreateReviewRequest,
  CreateTicketRequest,
  CreateUpdateRequest,
  ExchangeEnrollmentRequest,
  ListActivityParams,
  ListTicketsParams,
  MasterLease,
  MasterLeaseRequest,
  MasterPauseRequest,
  ReviewDecisionRequest,
  RevokeSessionLeaseRequest,
  SessionCredentialResponse,
  SetTicketBlockedRequest,
} from "./types";
import {
  assertActivityResponse,
  assertAgent,
  assertAgentListResponse,
  assertAssignment,
  assertCreateEnrollmentResponse,
  assertMasterLease,
  assertMasterPanelResponse,
  assertMutationRequest,
  assertOverviewResponse,
  assertReview,
  assertSessionCredentialResponse,
  assertTicket,
  assertTicketDetailResponse,
  assertTicketListResponse,
  assertTicketUpdate,
} from "./validate";

/**
 * Typed client for the Ticket Board V1 dashboard, against the T-178 frozen
 * contract (docs/contracts/openapi.yaml). Fixture-replay tested only — see
 * tests/ui/api/ and README.md. No route here has ever been called against a
 * live server; wiring that up is T-184's job.
 */
export class BoardClient {
  constructor(private readonly config: BoardClientConfig) {}

  // --------------------------------------------------------------- overview

  async getOverview(requestId?: string): Promise<OverviewResponse> {
    const body = await boardRequest<OverviewResponse>(this.config, {
      method: "GET",
      path: "overview",
      expectedStatus: 200,
      requestId,
    });
    assertOverviewResponse(body);
    return body;
  }

  // ---------------------------------------------------------------- tickets

  async listTickets(params: ListTicketsParams = {}, requestId?: string): Promise<TicketListResponse> {
    const body = await boardRequest<TicketListResponse>(this.config, {
      method: "GET",
      path: "tickets",
      query: params,
      expectedStatus: 200,
      requestId,
    });
    assertTicketListResponse(body);
    return body;
  }

  async getTicket(ticketId: string, requestId?: string): Promise<TicketDetailResponse> {
    const body = await boardRequest<TicketDetailResponse>(this.config, {
      method: "GET",
      path: `tickets/${encodeURIComponent(ticketId)}`,
      expectedStatus: 200,
      requestId,
    });
    assertTicketDetailResponse(body);
    return body;
  }

  async createTicket(request: CreateTicketRequest): Promise<Ticket> {
    assertMutationRequest(request as unknown as Record<string, unknown>, [], "CreateTicketRequest");
    const body = await boardRequest<Ticket>(this.config, {
      method: "POST",
      path: "tickets",
      body: request,
      expectedStatus: 201,
      requestId: request.request_id,
    });
    assertTicket(body);
    return body;
  }

  async claimTicket(ticketId: string, request: ClaimTicketRequest): Promise<Ticket> {
    assertMutationRequest(request as unknown as Record<string, unknown>, ["expected_version"], "ClaimTicketRequest");
    const body = await boardRequest<Ticket>(this.config, {
      method: "POST",
      path: `tickets/${encodeURIComponent(ticketId)}/claim`,
      body: request,
      expectedStatus: 200,
      requestId: request.request_id,
    });
    assertTicket(body);
    return body;
  }

  async createTicketUpdate(ticketId: string, request: CreateUpdateRequest): Promise<TicketUpdate> {
    assertMutationRequest(request as unknown as Record<string, unknown>, [], "CreateUpdateRequest");
    const body = await boardRequest<TicketUpdate>(this.config, {
      method: "POST",
      path: `tickets/${encodeURIComponent(ticketId)}/updates`,
      body: request,
      expectedStatus: 201,
      requestId: request.request_id,
    });
    assertTicketUpdate(body);
    return body;
  }

  async requestReview(ticketId: string, request: CreateReviewRequest): Promise<Review> {
    assertMutationRequest(request as unknown as Record<string, unknown>, ["expected_version"], "CreateReviewRequest");
    const body = await boardRequest<Review>(this.config, {
      method: "POST",
      path: `tickets/${encodeURIComponent(ticketId)}/reviews`,
      body: request,
      expectedStatus: 201,
      requestId: request.request_id,
    });
    assertReview(body);
    return body;
  }

  /** Accept or reject. "Done" is `decideReview(id, reviewId, { decision: "accept", ... })` — there is no separate done route. */
  async decideReview(ticketId: string, reviewId: string, request: ReviewDecisionRequest): Promise<Review> {
    assertMutationRequest(request as unknown as Record<string, unknown>, ["expected_version"], "ReviewDecisionRequest");
    const body = await boardRequest<Review>(this.config, {
      method: "POST",
      path: `tickets/${encodeURIComponent(ticketId)}/reviews/${encodeURIComponent(reviewId)}/decision`,
      body: request,
      expectedStatus: 200,
      requestId: request.request_id,
    });
    assertReview(body);
    return body;
  }

  async setTicketBlocked(ticketId: string, request: SetTicketBlockedRequest): Promise<Ticket> {
    assertMutationRequest(request as unknown as Record<string, unknown>, ["expected_version"], "SetTicketBlockedRequest");
    const body = await boardRequest<Ticket>(this.config, {
      method: "POST",
      path: `tickets/${encodeURIComponent(ticketId)}/blocked`,
      body: request,
      expectedStatus: 200,
      requestId: request.request_id,
    });
    assertTicket(body);
    return body;
  }

  // ----------------------------------------------------------------- agents

  async listAgents(requestId?: string): Promise<AgentListResponse> {
    const body = await boardRequest<AgentListResponse>(this.config, {
      method: "GET",
      path: "agents",
      expectedStatus: 200,
      requestId,
    });
    assertAgentListResponse(body);
    return body;
  }

  /** Operator sessions only; an agent token gets 403 `agent_token_insufficient`. */
  async createEnrollment(request: CreateEnrollmentRequest): Promise<CreateEnrollmentResponse> {
    assertMutationRequest(request as unknown as Record<string, unknown>, [], "CreateEnrollmentRequest");
    const body = await boardRequest<CreateEnrollmentResponse>(this.config, {
      method: "POST",
      path: "enrollments",
      body: request,
      expectedStatus: 201,
      requestId: request.request_id,
    });
    assertCreateEnrollmentResponse(body);
    return body;
  }

  /** Unauthenticated by credential — the code is the proof (docs/contracts/openapi.yaml `/sessions`). */
  async exchangeEnrollment(request: ExchangeEnrollmentRequest): Promise<SessionCredentialResponse> {
    assertMutationRequest(request as unknown as Record<string, unknown>, [], "ExchangeEnrollmentRequest");
    const body = await boardRequest<SessionCredentialResponse>(this.config, {
      method: "POST",
      path: "sessions",
      body: request,
      expectedStatus: 201,
      requestId: request.request_id,
    });
    assertSessionCredentialResponse(body);
    return body;
  }

  /** Preserves the agent's work; the ticket is not reassigned by this call. */
  async revokeSessionLease(agentId: string, request: RevokeSessionLeaseRequest): Promise<Agent> {
    assertMutationRequest(request as unknown as Record<string, unknown>, ["expected_version"], "RevokeSessionLeaseRequest");
    const body = await boardRequest<Agent>(this.config, {
      method: "DELETE",
      path: `agents/${encodeURIComponent(agentId)}/session-lease`,
      body: request,
      expectedStatus: 200,
      requestId: request.request_id,
    });
    assertAgent(body);
    return body;
  }

  // ----------------------------------------------------------------- master

  async getMasterPanel(requestId?: string): Promise<MasterPanelResponse> {
    const body = await boardRequest<MasterPanelResponse>(this.config, {
      method: "GET",
      path: "master",
      expectedStatus: 200,
      requestId,
    });
    assertMasterPanelResponse(body);
    return body;
  }

  async takeMasterLease(request: MasterLeaseRequest): Promise<MasterLease> {
    assertMutationRequest(request as unknown as Record<string, unknown>, ["expected_epoch"], "MasterLeaseRequest");
    const body = await boardRequest<MasterLease>(this.config, {
      method: "POST",
      path: "master/lease",
      body: request,
      expectedStatus: 200,
      requestId: request.request_id,
    });
    assertMasterLease(body);
    return body;
  }

  async setMasterPaused(request: MasterPauseRequest): Promise<MasterLease> {
    assertMutationRequest(request as unknown as Record<string, unknown>, ["lease_epoch"], "MasterPauseRequest");
    const body = await boardRequest<MasterLease>(this.config, {
      method: "POST",
      path: "master/pause",
      body: request,
      expectedStatus: 200,
      requestId: request.request_id,
    });
    assertMasterLease(body);
    return body;
  }

  /** Reservation shows as "Queued -- waiting for agent", never "Working" (T-183 copy rule). */
  async createAssignment(request: CreateAssignmentRequest): Promise<Assignment> {
    assertMutationRequest(
      request as unknown as Record<string, unknown>,
      ["lease_epoch", "expected_version"],
      "CreateAssignmentRequest",
    );
    const body = await boardRequest<Assignment>(this.config, {
      method: "POST",
      path: "assignments",
      body: request,
      expectedStatus: 201,
      requestId: request.request_id,
    });
    assertAssignment(body);
    return body;
  }

  // --------------------------------------------------------------- activity

  async listActivity(params: ListActivityParams = {}, requestId?: string): Promise<ActivityResponse> {
    const body = await boardRequest<ActivityResponse>(this.config, {
      method: "GET",
      path: "activity",
      query: params,
      expectedStatus: 200,
      requestId,
    });
    assertActivityResponse(body);
    return body;
  }
}
