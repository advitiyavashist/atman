import { describe, expect, it } from "vitest";
import { BoardClient } from "../../../ui/src/api/client";
import { BoardError } from "../../../ui/src/api/errors";
import type { ClaimTicketRequest } from "../../../ui/src/api/types";
import type { TicketDetailResponse } from "../../../ui/src/types";
import { loadFixture } from "./support/fixtures";
import { baseConfig, jsonResponse, sequenceFetch } from "./support/mock-fetch";

// The four required negative checks (T-203 acceptance bar): each of these
// must FAIL — the client must refuse, or must surface the server's refusal
// as a BoardError, never silently succeed or crash unhandled.

describe("mutation checks — each of these must fail", () => {
  it("never serializes an `actor` field on any mutation body, and refuses one a caller forces in", async () => {
    const validRequest = loadFixture("tickets/request-claim.json") as ClaimTicketRequest;
    const response = loadFixture<TicketDetailResponse>("tickets/detail-claimed.json").ticket;
    const fetchMock = sequenceFetch([jsonResponse(response, 200)]);
    const client = new BoardClient(baseConfig(fetchMock));

    // Sanity: a normal, correctly-typed call never has an actor key.
    await client.claimTicket("DEMO-13", validRequest);
    const [, sentInit] = fetchMock.mock.calls[0];
    expect(JSON.parse(sentInit!.body as string)).not.toHaveProperty("actor");

    // A caller that reaches in with `as unknown as` and forces one in must be
    // rejected before it reaches fetch — ClaimTicketRequest has no `actor`
    // property, so this is only reachable by bypassing the type system, the
    // same way a hostile or buggy caller would.
    const withActor = { ...validRequest, actor: { type: "human", id: "mem_x", display_name: "x" } } as unknown as ClaimTicketRequest;
    const fetchMock2 = sequenceFetch([jsonResponse(response, 200)]);
    const client2 = new BoardClient(baseConfig(fetchMock2));

    await expect(client2.claimTicket("DEMO-13", withActor)).rejects.toThrow(BoardError);
    expect(fetchMock2).not.toHaveBeenCalled();
  });

  it("rejects a non-numeric expected_version before sending, instead of letting a wire type violation through", async () => {
    const request = loadFixture("tickets/request-claim.json") as ClaimTicketRequest;
    const corrupted = { ...request, expected_version: "1" } as unknown as ClaimTicketRequest;
    const fetchMock = sequenceFetch([]);
    const client = new BoardClient(baseConfig(fetchMock));

    await expect(client.claimTicket("DEMO-13", corrupted)).rejects.toMatchObject({
      code: "client_malformed_request",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fails when the server returns a status the contract does not declare for this call (201 for a route documented as 200)", async () => {
    const request = loadFixture("tickets/request-claim.json") as ClaimTicketRequest;
    const response = loadFixture<TicketDetailResponse>("tickets/detail-claimed.json").ticket;
    // claimTicket is documented '200' in openapi.yaml; the mock server sends 201.
    const fetchMock = sequenceFetch([jsonResponse(response, 201)]);
    const client = new BoardClient(baseConfig(fetchMock));

    await expect(client.claimTicket("DEMO-13", request)).rejects.toMatchObject({
      code: "unexpected_status",
      status: 201,
    });
  });

  it("a body missing a required field fails shape validation instead of reaching the caller half-formed", async () => {
    const goodTicket = loadFixture<TicketDetailResponse>("tickets/detail-claimed.json").ticket as unknown as Record<string, unknown>;
    const { version: _version, ...ticketWithoutVersion } = goodTicket;
    void _version;
    const malformedDetail = {
      ticket: ticketWithoutVersion,
      updates: [],
      reviews: [],
      assignment: null,
      dependencies: [],
      available_actions: [],
      stream: { state: "live", snapshot_version: 1, as_of: "2026-01-01T00:00:00Z", last_event_id: null },
    };
    const fetchMock = sequenceFetch([jsonResponse(malformedDetail, 200)]);
    const client = new BoardClient(baseConfig(fetchMock));

    await expect(client.getTicket("DEMO-13")).rejects.toMatchObject({ code: "invalid_response_shape" });
  });
});

describe("BoardError", () => {
  it("parses a 409 ticket_version_conflict envelope and surfaces both versions in one shot", async () => {
    const envelope = loadFixture("errors/409-ticket-version-conflict.json");
    const fetchMock = sequenceFetch([jsonResponse(envelope, 409)]);
    const client = new BoardClient(baseConfig(fetchMock));
    const request = loadFixture("tickets/request-claim.json") as ClaimTicketRequest;

    try {
      await client.claimTicket("DEMO-13", request);
      expect.unreachable();
    } catch (err) {
      expect(err).toBeInstanceOf(BoardError);
      const boardError = err as BoardError;
      expect(boardError.code).toBe("ticket_version_conflict");
      expect(boardError.status).toBe(409);
      expect(boardError.versionConflict()).toEqual({ expected_version: 4, actual_version: 5 });
    }
  });

  it("versionConflict() returns null for a non-version-conflict error", async () => {
    const envelope = loadFixture("errors/403-forbidden-scope.json");
    const fetchMock = sequenceFetch([jsonResponse(envelope, 403)]);
    const client = new BoardClient(baseConfig(fetchMock));

    try {
      await client.getOverview();
      expect.unreachable();
    } catch (err) {
      expect((err as BoardError).versionConflict()).toBeNull();
    }
  });

  it("a 422 dependency_unmet claim failure surfaces its code without needing a special case", async () => {
    const envelope = loadFixture("errors/422-dependency-unmet.json");
    const fetchMock = sequenceFetch([jsonResponse(envelope, 422)]);
    const client = new BoardClient(baseConfig(fetchMock));
    const request = loadFixture("tickets/request-claim.json") as ClaimTicketRequest;

    await expect(client.claimTicket("DEMO-13", request)).rejects.toMatchObject({ code: "dependency_unmet", status: 422 });
  });

  it("a network failure (fetch rejects) surfaces as BoardError, not an unhandled exception type", async () => {
    const client = new BoardClient(
      baseConfig(
        async () => {
          throw new TypeError("fetch failed");
        },
      ),
    );

    await expect(client.getOverview()).rejects.toMatchObject({ code: "network_error" });
  });
});
