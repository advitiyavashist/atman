/**
 * The API client: where it will talk, what it sends, and what it refuses.
 *
 * The app is allowed exactly one network peer — `atm ui` on loopback — so the
 * refusals are as much the contract as the requests are.
 */

import { afterEach, describe, expect, it } from "vitest";

import { ApiError, AtmanApi, isLoopbackOrigin, resolveOrigin } from "../../ui/src/api/client";
import type { Session } from "../../ui/src/api/types";
import { fixture } from "./support/schema";

function meta(name: string, content: string) {
  const el = document.createElement("meta");
  el.setAttribute("name", name);
  el.setAttribute("content", content);
  document.head.appendChild(el);
  return el;
}

afterEach(() => {
  for (const el of Array.from(document.querySelectorAll('meta[name^="atman-"]'))) el.remove();
});

describe("where the app talks", () => {
  it("accepts only loopback origins", () => {
    for (const ok of ["http://127.0.0.1:8765", "http://localhost:5173", "https://127.0.0.1:9000", "http://[::1]:8765"]) {
      expect(isLoopbackOrigin(ok), ok).toBe(true);
    }
    for (const no of ["http://example.test", "http://10.0.0.5:8765", "ftp://127.0.0.1", "127.0.0.1:8765", ""]) {
      expect(isLoopbackOrigin(no), no).toBe(false);
    }
  });

  it("uses the meta tag the served bundle carries, and is then same-origin", () => {
    meta("atman-api", "/api/v1");
    const origin = resolveOrigin("");
    expect(origin.sameOrigin).toBe(true);
    expect(origin.api.endsWith("/api/v1")).toBe(true);
    expect(origin.refused).toBeUndefined();
  });

  it("reads the write token from the page when atm ui served it", () => {
    meta("atman-api", "/api/v1");
    meta("atman-token", "x".repeat(32));
    expect(new AtmanApi().writeToken).toBe("x".repeat(32));
  });

  it("defaults the dev server to the documented local port", () => {
    const origin = resolveOrigin("");
    expect(origin.api).toBe("http://127.0.0.1:8765/api/v1");
    expect(origin.sameOrigin).toBe(false);
  });

  it("takes ?api= only when it is loopback, and says why when it refuses", () => {
    expect(resolveOrigin("?api=http://127.0.0.1:9999").api).toBe("http://127.0.0.1:9999/api/v1");
    const refused = resolveOrigin("?api=http://example.test");
    expect(refused.api).toBe("");
    expect(refused.refused).toContain("not a loopback origin");
  });

  it("makes no request at all with a refused origin", async () => {
    let called = 0;
    const api = new AtmanApi(resolveOrigin("?api=http://example.test"), () => {
      called += 1;
      return Promise.reject(new Error("should never be called"));
    });
    await expect(api.projects()).rejects.toThrow(/not a loopback origin/);
    expect(called).toBe(0);
  });
});

describe("what it sends", () => {
  it("sends X-Atman-Client on reads, which is what forces the preflight", async () => {
    const seen: Array<{ url: string; init?: RequestInit }> = [];
    const body = fixture<Session>("session.json", { token: "y".repeat(32) });
    const api = new AtmanApi(resolveOrigin(""), (url, init) => {
      seen.push({ url: String(url), init });
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
    });
    const session = await api.session();
    expect(session.token).toBe("y".repeat(32));
    expect(api.writeToken).toBe("y".repeat(32));
    const headers = new Headers(seen[0].init?.headers);
    expect(headers.get("X-Atman-Client")).toBe("app");
    expect(seen[0].url).toBe("http://127.0.0.1:8765/api/v1/session");
  });

  it("puts the project in the query and never in the path", async () => {
    const seen: string[] = [];
    const api = new AtmanApi(resolveOrigin(""), (url) => {
      seen.push(String(url));
      return Promise.resolve(new Response(JSON.stringify(fixture("plan.json")), { status: 200 }));
    });
    await api.plan("demo");
    expect(seen[0]).toBe("http://127.0.0.1:8765/api/v1/plan?project=demo");
  });

  it("sends the launch token on a post, and the operator as from", async () => {
    meta("atman-api", "/api/v1");
    meta("atman-token", "z".repeat(32));
    let sent: RequestInit | undefined;
    const api = new AtmanApi(resolveOrigin(""), (_url, init) => {
      sent = init;
      return Promise.resolve(new Response(JSON.stringify({ ok: true, posted: "m7" }), { status: 200 }));
    });
    await api.postMessage({ from: "ada", text: "status?", to: "planner" });
    expect(new Headers(sent?.headers).get("X-Atman-Token")).toBe("z".repeat(32));
    expect(JSON.parse(String(sent?.body))).toMatchObject({ from: "ada", text: "status?", to: "planner" });
  });

  it("refuses to post with no write token rather than sending a request that cannot work", async () => {
    let called = 0;
    const api = new AtmanApi(resolveOrigin(""), () => {
      called += 1;
      return Promise.resolve(new Response("{}", { status: 200 }));
    });
    await expect(api.postMessage({ from: "ada", text: "hi" })).rejects.toThrow(/no write token/);
    expect(called).toBe(0);
  });

  it("explains that POST /msg is same-origin only when the network refuses it", async () => {
    // From a dev origin the route sends no CORS headers and refuses an Origin
    // that is not its Host, so the browser's failure needs this explanation
    // rather than "network error".
    const api = new AtmanApi({ ...resolveOrigin(""), sameOrigin: false }, () => Promise.reject(new TypeError("Failed to fetch")));
    (api as unknown as { token: string }).token = "q".repeat(32);
    await expect(api.postMessage({ from: "ada", text: "hi" })).rejects.toThrow(/same-origin only/);
  });
});

describe("what it reports", () => {
  it("passes the server's own error string through", async () => {
    const api = new AtmanApi(resolveOrigin(""), () =>
      Promise.resolve(new Response(JSON.stringify({ error: "unknown project nope" }), { status: 404 })),
    );
    await expect(api.plan("nope")).rejects.toMatchObject({ message: "unknown project nope", status: 404 });
  });

  it("says the board may not be running when nothing answers", async () => {
    const api = new AtmanApi(resolveOrigin(""), () => Promise.reject(new TypeError("Failed to fetch")));
    await expect(api.board()).rejects.toThrow(/is atm ui running/);
  });

  it("does not pretend a non-JSON answer is data", async () => {
    const api = new AtmanApi(resolveOrigin(""), () => Promise.resolve(new Response("<html>", { status: 200 })));
    await expect(api.lead()).rejects.toBeInstanceOf(ApiError);
  });
});
