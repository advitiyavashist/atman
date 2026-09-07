import { describe, expect, it } from "vitest";
import { boardFetch } from "./support/render-live";

describe("render-live mock validates POST+201 request bodies against openapi.yaml", () => {
  it("returns 400 for a schema-invalid body on a contract POST+201 route, not 201", async () => {
    const harness = boardFetch({ "POST /tickets": { id: "DEMO-99", state: "open", version: 1 } });
    const response = await harness.fetchMock("http://127.0.0.1:4319/tickets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // CreateTicketRequest requires request_id; the contract would 400 this.
      body: JSON.stringify({ title: "missing request_id" }),
    });

    expect(response.status).not.toBe(201);
    expect(response.status).toBe(400);
    const payload = await response.json();
    expect(payload.error.code).toBe("malformed_request");
  });

  it("still returns 201 when the body matches the contract schema", async () => {
    const harness = boardFetch({ "POST /tickets": { id: "DEMO-99", state: "open", version: 1 } });
    const response = await harness.fetchMock("http://127.0.0.1:4319/tickets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request_id: "0f1e2d3c-4b5a-4968-8776-655443322110",
        title: "Valid ticket",
      }),
    });

    expect(response.status).toBe(201);
  });
});
