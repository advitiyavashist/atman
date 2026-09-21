/**
 * The write side of the client, validated against the contract's *request*
 * schemas — not only its responses.
 *
 * `lead-request.json` existed in the schema registry and nothing used it,
 * which meant the one body this app sends to `/api/v1/lead` was unchecked in
 * both directions. A body the server would refuse should fail here first.
 */

import { describe, expect, it } from "vitest";

import { ApiError, AtmanApi, resolveOrigin } from "../../ui/src/api/client";
import { expectValid, fixture } from "./support/schema";

const TOKEN = "t".repeat(32);

function apiWith(answer: (body: unknown) => { status: number; body: unknown }) {
  const sent: { body: unknown; headers: Headers; url: string; method?: string }[] = [];
  const api = new AtmanApi(resolveOrigin(""), (url, init) => {
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    sent.push({ body, headers: new Headers(init?.headers), url: String(url), method: init?.method });
    const out = answer(body);
    return Promise.resolve(new Response(JSON.stringify(out.body), { status: out.status }));
  });
  (api as unknown as { token: string }).token = TOKEN;
  return { api, sent };
}

describe("POST /api/v1/lead", () => {
  it("sends a body that satisfies lead-request.json", async () => {
    const { api, sent } = apiWith(() => ({
      status: 200,
      body: fixture("lead-response.json", { ok: true, lead: "planner", harness: "codex", capability: "answers on its next turn", project: "alpha" }),
    }));
    await api.setLead("planner", "alpha");
    expectValid("lead-request.json", sent[0].body);
    expect(sent[0].body).toEqual({ seat: "planner", project: "alpha" });
    expect(sent[0].url).toBe("http://127.0.0.1:8765/api/v1/lead");
    expect(sent[0].method).toBe("POST");
  });

  it("omits project rather than sending an empty one, which the schema would refuse", async () => {
    const { api, sent } = apiWith(() => ({
      status: 200,
      body: fixture("lead-response.json", { ok: true, lead: "planner", harness: "unknown", capability: "answers on its next turn", project: "alpha" }),
    }));
    await api.setLead("planner");
    expectValid("lead-request.json", sent[0].body);
    expect(sent[0].body).toEqual({ seat: "planner" });
    // lead-request.json is closed: an extra key is a 400 from the server.
    expect(() => expectValid("lead-request.json", { seat: "planner", from: "ada" })).toThrow();
    expect(() => expectValid("lead-request.json", { project: "alpha" })).toThrow();
  });

  it("carries the launch token and the client header, never a from", async () => {
    const { api, sent } = apiWith(() => ({
      status: 200,
      body: fixture("lead-response.json", { ok: true, lead: "coder", harness: "claude", capability: "takes mid-run messages", project: "alpha" }),
    }));
    await api.setLead("coder", "alpha");
    expect(sent[0].headers.get("X-Atman-Token")).toBe(TOKEN);
    expect(sent[0].headers.get("X-Atman-Client")).toBe("app");
    expect(sent[0].headers.get("Content-Type")).toBe("application/json");
    expect(Object.keys(sent[0].body as object)).not.toContain("from");
  });

  it("reads back the lead, harness and capability the server recorded", async () => {
    const { api } = apiWith(() => ({
      status: 200,
      body: fixture("lead-response.json", { ok: true, lead: "coder", harness: "claude", capability: "takes mid-run messages", project: "alpha" }),
    }));
    const out = await api.setLead("coder", "alpha");
    expect(out).toMatchObject({ ok: true, lead: "coder", harness: "claude", capability: "takes mid-run messages" });
  });

  it("passes the server's refusal through, word for word", async () => {
    // What the route answers when atm ui was started without --operator.
    const { api } = apiWith(() => ({
      status: 400,
      body: { ok: false, error: "only the operator picks the lead: set an operator: atm ui --operator <name>" },
    }));
    await expect(api.setLead("planner", "alpha")).rejects.toMatchObject({
      message: "only the operator picks the lead: set an operator: atm ui --operator <name>",
      status: 400,
    });
  });

  it("treats a 200 that does not say ok as a failure, not a success", async () => {
    const { api } = apiWith(() => ({ status: 200, body: { lead: "planner" } }));
    await expect(api.setLead("planner")).rejects.toBeInstanceOf(ApiError);
  });

  it("does not pretend a non-JSON answer was a write", async () => {
    const api = new AtmanApi(resolveOrigin(""), () => Promise.resolve(new Response("<html>", { status: 200 })));
    (api as unknown as { token: string }).token = TOKEN;
    await expect(api.setLead("planner")).rejects.toThrow(/not JSON/);
  });
});

describe("POST /msg", () => {
  it("sends the operator as from, with the token, and nothing else invented", async () => {
    const { api, sent } = apiWith(() => ({ status: 200, body: { ok: true, posted: "m7" } }));
    await api.postMessage({ from: "ada", text: "status on the cut?", to: "planner", re: "T-3" });
    expect(sent[0].url).toBe("http://127.0.0.1:8765/msg");
    expect(sent[0].headers.get("X-Atman-Token")).toBe(TOKEN);
    expect(sent[0].body).toEqual({ from: "ada", text: "status on the cut?", to: "planner", re: "T-3" });
  });

  it("passes the route's own refusal through", async () => {
    const { api } = apiWith(() => ({ status: 400, body: { ok: false, error: "from must be a registered agent" } }));
    await expect(api.postMessage({ from: "nobody", text: "hi" })).rejects.toMatchObject({
      message: "from must be a registered agent",
    });
  });
});
