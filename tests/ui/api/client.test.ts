import { describe, expect, it } from "vitest";
import { BoardClient } from "../../../ui/src/api/client";
import type {
  Agent,
  Assignment,
  Review,
  Ticket,
  TicketUpdate,
} from "../../../ui/src/types";
import type {
  ActivityResponse,
  AgentListResponse,
  MasterPanelResponse,
  OverviewResponse,
  TicketDetailResponse,
  TicketListResponse,
} from "../../../ui/src/types";
import type { CreateEnrollmentResponse, MasterLease, SessionCredentialResponse } from "../../../ui/src/api/types";
import { loadFixture } from "./support/fixtures";
import { baseConfig, jsonResponse, lastCallHeaders, lastCallUrl, sequenceFetch } from "./support/mock-fetch";

describe("BoardClient reads", () => {
  it("getOverview parses the canonical fixture and sends X-Project-Id", async () => {
    const fixture = loadFixture<OverviewResponse>("overview/populated.json");
    const fetchMock = sequenceFetch([jsonResponse(fixture, 200)]);
    const client = new BoardClient(baseConfig(fetchMock));

    const result = await client.getOverview();

    expect(result).toEqual(fixture);
    expect(lastCallUrl(fetchMock)).toBe("http://127.0.0.1:4319/overview");
    expect(lastCallHeaders(fetchMock).get("X-Project-Id")).toBe("prj_demo0001");
  });

  it("listTickets encodes query params and returns the fixture untouched", async () => {
    const fixture = loadFixture<TicketListResponse>("tickets/list-populated.json");
    const fetchMock = sequenceFetch([jsonResponse(fixture, 200)]);
    const client = new BoardClient(baseConfig(fetchMock));

    const result = await client.listTickets({ state: "claimed", limit: 25, dependency_blocked: false });

    expect(result).toEqual(fixture);
    const url = new URL(lastCallUrl(fetchMock));
    expect(url.pathname).toBe("/tickets");
    expect(url.searchParams.get("state")).toBe("claimed");
    expect(url.searchParams.get("limit")).toBe("25");
    expect(url.searchParams.get("dependency_blocked")).toBe("false");
  });

  it("getTicket reads the detail fixture at the ticket-scoped path", async () => {
    const fixture = loadFixture<TicketDetailResponse>("tickets/detail-claimed.json");
    const fetchMock = sequenceFetch([jsonResponse(fixture, 200)]);
    const client = new BoardClient(baseConfig(fetchMock));

    const result = await client.getTicket("DEMO-13");

    expect(result).toEqual(fixture);
    expect(new URL(lastCallUrl(fetchMock)).pathname).toBe("/tickets/DEMO-13");
  });

  it("listAgents parses the canonical fixture", async () => {
    const fixture = loadFixture<AgentListResponse>("agents/list-populated.json");
    const fetchMock = sequenceFetch([jsonResponse(fixture, 200)]);
    const client = new BoardClient(baseConfig(fetchMock));

    expect(await client.listAgents()).toEqual(fixture);
  });

  it("getMasterPanel parses the canonical fixture", async () => {
    const fixture = loadFixture<MasterPanelResponse>("master/panel-active.json");
    const fetchMock = sequenceFetch([jsonResponse(fixture, 200)]);
    const client = new BoardClient(baseConfig(fetchMock));

    expect(await client.getMasterPanel()).toEqual(fixture);
  });

  it("listActivity encodes cursor/limit/subject_type and parses the fixture", async () => {
    const fixture = loadFixture<ActivityResponse>("activity/populated.json");
    const fetchMock = sequenceFetch([jsonResponse(fixture, 200)]);
    const client = new BoardClient(baseConfig(fetchMock));

    const result = await client.listActivity({ subject_type: "ticket", limit: 10 });

    expect(result).toEqual(fixture);
    const url = new URL(lastCallUrl(fetchMock));
    expect(url.pathname).toBe("/activity");
    expect(url.searchParams.get("subject_type")).toBe("ticket");
  });
});

describe("BoardClient mutations — request body matches the canonical fixture, response is parsed", () => {
  it("createTicket sends request-create.json verbatim and returns a Ticket", async () => {
    const request = loadFixture("tickets/request-create.json") as Parameters<BoardClient["createTicket"]>[0];
    const response = loadFixture<TicketDetailResponse>("tickets/detail-claimed.json").ticket;
    const fetchMock = sequenceFetch([jsonResponse(response, 201)]);
    const client = new BoardClient(baseConfig(fetchMock));

    const result = await client.createTicket(request);

    expect(result).toEqual(response);
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init!.body as string)).toEqual(request);
    expect(init!.method).toBe("POST");
  });

  it("claimTicket sends request-claim.json verbatim to the claim path", async () => {
    const request = loadFixture("tickets/request-claim.json") as Parameters<BoardClient["claimTicket"]>[1];
    const response = loadFixture<TicketDetailResponse>("tickets/detail-claimed.json").ticket as Ticket;
    const fetchMock = sequenceFetch([jsonResponse(response, 200)]);
    const client = new BoardClient(baseConfig(fetchMock));

    const result = await client.claimTicket("DEMO-13", request);

    expect(result).toEqual(response);
    expect(new URL(lastCallUrl(fetchMock)).pathname).toBe("/tickets/DEMO-13/claim");
  });

  it("createTicketUpdate sends request-update.json and returns a TicketUpdate", async () => {
    const request = loadFixture("tickets/request-update.json") as Parameters<BoardClient["createTicketUpdate"]>[1];
    const response = loadFixture<TicketDetailResponse>("tickets/detail-claimed.json").updates[0] as TicketUpdate;
    const fetchMock = sequenceFetch([jsonResponse(response, 201)]);
    const client = new BoardClient(baseConfig(fetchMock));

    const result = await client.createTicketUpdate("DEMO-13", request);

    expect(result).toEqual(response);
    expect(new URL(lastCallUrl(fetchMock)).pathname).toBe("/tickets/DEMO-13/updates");
  });

  it("requestReview sends request-review.json and returns a requested Review", async () => {
    const request = loadFixture("tickets/request-review.json") as Parameters<BoardClient["requestReview"]>[1];
    const response = loadFixture<TicketDetailResponse>("tickets/detail-review-pending.json").reviews[0] as Review;
    const fetchMock = sequenceFetch([jsonResponse(response, 201)]);
    const client = new BoardClient(baseConfig(fetchMock));

    const result = await client.requestReview("DEMO-13", request);

    expect(result).toEqual(response);
    expect(result.state).toBe("requested");
  });

  it("decideReview(accept) sends request-review-accept.json and returns an accepted Review", async () => {
    const request = loadFixture("tickets/request-review-accept.json") as Parameters<BoardClient["decideReview"]>[2];
    const response = loadFixture<TicketDetailResponse>("tickets/detail-accepted.json").reviews[0] as Review;
    const fetchMock = sequenceFetch([jsonResponse(response, 200)]);
    const client = new BoardClient(baseConfig(fetchMock));

    const result = await client.decideReview("DEMO-13", "rev_00000001", request);

    expect(result).toEqual(response);
    expect(result.state).toBe("accepted");
    expect(new URL(lastCallUrl(fetchMock)).pathname).toBe("/tickets/DEMO-13/reviews/rev_00000001/decision");
  });

  it("decideReview(reject) sends request-review-reject.json and returns a rejected Review", async () => {
    const request = loadFixture("tickets/request-review-reject.json") as Parameters<BoardClient["decideReview"]>[2];
    const response = loadFixture<TicketDetailResponse>("tickets/detail-review-rejected.json").reviews[0] as Review;
    const fetchMock = sequenceFetch([jsonResponse(response, 200)]);
    const client = new BoardClient(baseConfig(fetchMock));

    const result = await client.decideReview("DEMO-13", "rev_00000002", request);

    expect(result.state).toBe("rejected");
  });

  it("setTicketBlocked sends request-set-blocked.json and returns the blocked Ticket", async () => {
    const request = loadFixture("tickets/request-set-blocked.json") as Parameters<BoardClient["setTicketBlocked"]>[1];
    const response = loadFixture<TicketDetailResponse>("tickets/detail-blocked.json").ticket as Ticket;
    const fetchMock = sequenceFetch([jsonResponse(response, 200)]);
    const client = new BoardClient(baseConfig(fetchMock));

    const result = await client.setTicketBlocked("DEMO-13", request);

    expect(result.state).toBe("blocked");
    expect(result.blocked_reason).toBe(request.reason);
  });

  it("createEnrollment sends request-enrollment.json and returns response-enrollment.json", async () => {
    const request = loadFixture("agents/request-enrollment.json") as Parameters<BoardClient["createEnrollment"]>[0];
    const response = loadFixture<CreateEnrollmentResponse>("agents/response-enrollment.json");
    const fetchMock = sequenceFetch([jsonResponse(response, 201)]);
    const client = new BoardClient(baseConfig(fetchMock));

    const result = await client.createEnrollment(request);

    expect(result).toEqual(response);
    expect(new URL(lastCallUrl(fetchMock)).pathname).toBe("/enrollments");
  });

  it("exchangeEnrollment sends request-session-exchange.json and returns response-session-credential.json", async () => {
    const request = loadFixture("agents/request-session-exchange.json") as Parameters<BoardClient["exchangeEnrollment"]>[0];
    const response = loadFixture<SessionCredentialResponse>("agents/response-session-credential.json");
    const fetchMock = sequenceFetch([jsonResponse(response, 201)]);
    const client = new BoardClient(baseConfig(fetchMock));

    const result = await client.exchangeEnrollment(request);

    expect(result).toEqual(response);
    expect(new URL(lastCallUrl(fetchMock)).pathname).toBe("/sessions");
  });

  it("revokeSessionLease sends request-revoke-lease.json as a DELETE with a body", async () => {
    const request = loadFixture("agents/request-revoke-lease.json") as Parameters<BoardClient["revokeSessionLease"]>[1];
    const response = loadFixture<SessionCredentialResponse>("agents/response-session-credential.json").agent as Agent;
    const fetchMock = sequenceFetch([jsonResponse(response, 200)]);
    const client = new BoardClient(baseConfig(fetchMock));

    const result = await client.revokeSessionLease("agt_backend01", request);

    expect(result).toEqual(response);
    const [, init] = fetchMock.mock.calls[0];
    expect(init!.method).toBe("DELETE");
    expect(JSON.parse(init!.body as string)).toEqual(request);
    expect(new URL(lastCallUrl(fetchMock)).pathname).toBe("/agents/agt_backend01/session-lease");
  });

  it("takeMasterLease sends request-lease.json and returns response-lease.json", async () => {
    const request = loadFixture("master/request-lease.json") as Parameters<BoardClient["takeMasterLease"]>[0];
    const response = loadFixture<MasterLease>("master/response-lease.json");
    const fetchMock = sequenceFetch([jsonResponse(response, 200)]);
    const client = new BoardClient(baseConfig(fetchMock));

    expect(await client.takeMasterLease(request)).toEqual(response);
    expect(new URL(lastCallUrl(fetchMock)).pathname).toBe("/master/lease");
  });

  it("setMasterPaused sends request-pause.json to /master/pause", async () => {
    const request = loadFixture("master/request-pause.json") as Parameters<BoardClient["setMasterPaused"]>[0];
    const response = loadFixture<MasterLease>("master/response-lease.json");
    const fetchMock = sequenceFetch([jsonResponse(response, 200)]);
    const client = new BoardClient(baseConfig(fetchMock));

    await client.setMasterPaused(request);

    expect(new URL(lastCallUrl(fetchMock)).pathname).toBe("/master/pause");
  });

  it("createAssignment sends request-assignment.json and returns a queued Assignment", async () => {
    const request = loadFixture("master/request-assignment.json") as Parameters<BoardClient["createAssignment"]>[0];
    const response = loadFixture<Assignment>("master/response-assignment-queued.json");
    const fetchMock = sequenceFetch([jsonResponse(response, 201)]);
    const client = new BoardClient(baseConfig(fetchMock));

    const result = await client.createAssignment(request);

    expect(result).toEqual(response);
    expect(result.state).toBe("queued");
    expect(new URL(lastCallUrl(fetchMock)).pathname).toBe("/assignments");
  });
});

describe("BoardClient credentials", () => {
  it("sends the operator session by cookie (credentials: include) when no agent token is configured", async () => {
    const fixture = loadFixture<OverviewResponse>("overview/populated.json");
    const fetchMock = sequenceFetch([jsonResponse(fixture, 200)]);
    const client = new BoardClient(baseConfig(fetchMock));

    await client.getOverview();

    const [, init] = fetchMock.mock.calls[0];
    expect(init!.credentials).toBe("include");
    expect(lastCallHeaders(fetchMock).has("Authorization")).toBe(false);
  });

  it("sends a Bearer token and omits credentials when an agent token is configured", async () => {
    const fixture = loadFixture<AgentListResponse>("agents/list-populated.json");
    const fetchMock = sequenceFetch([jsonResponse(fixture, 200)]);
    const client = new BoardClient(baseConfig(fetchMock, { agentToken: "tok_abc123" }));

    await client.listAgents();

    const [, init] = fetchMock.mock.calls[0];
    expect(init!.credentials).toBeUndefined();
    expect(lastCallHeaders(fetchMock).get("Authorization")).toBe("Bearer tok_abc123");
  });
});
