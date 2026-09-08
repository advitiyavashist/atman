// @vitest-environment node
//
// Node, not jsdom: this file starts a real vite dev server, and vite's own
// module runner refuses to boot under jsdom (its TextEncoder produces a
// Uint8Array from a different realm, which trips vite's invariant check).
// Nothing here touches the DOM — it drives the host over HTTP the way a browser
// would.
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { randomUUID } from "node:crypto";
import { createServer, type ViteDevServer } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { boardHost } from "../../../ui/board-host";
import { BoardClient } from "../../../ui/src/api";
import { startBoard, type LiveBoard } from "./board-server";

/**
 * The dev host, end to end: a real vite server in front of a real board.
 *
 * This is the piece with no prior art in this repo, and the one the whole
 * dashboard depends on — the board sends no CORS headers, publishes its API at
 * the root, and refuses unsafe writes whose Origin is not its own, so without
 * this proxy a browser-served dashboard cannot read the board at all, let alone
 * write to it. Testing it through vite (rather than by calling the middleware
 * directly) is the point: the failure mode it guards against is a plugin that
 * is never actually installed into the server it claims to extend.
 */
describe("the dashboard host in front of a live board", () => {
  let board: LiveBoard;
  let vite: ViteDevServer;
  let origin: string;

  beforeAll(async () => {
    board = await startBoard();
    vite = await createServer({
      root: resolve(__dirname, "../../../ui"),
      configFile: false,
      logLevel: "silent",
      plugins: [react(), boardHost({ stateDir: board.stateDir })],
      server: { port: 0, host: "127.0.0.1" },
    });
    await vite.listen();
    const address = vite.httpServer!.address();
    const port = typeof address === "object" && address ? address.port : 0;
    origin = `http://127.0.0.1:${port}`;
  }, 60000);

  afterAll(async () => {
    await vite?.close();
    await board?.stop();
  });

  it("publishes the project id and CSRF token, and never the session token", async () => {
    const response = await fetch(`${origin}/__board/session`);
    expect(response.status).toBe(200);
    const body = (await response.json()) as Record<string, string>;

    expect(body.project_id).toBe(board.projectId);
    expect(body.csrf_token).toBe(board.csrfToken);
    expect(body.base_url).toBe("/api");
    // The credential stays in the host process. This is the property that makes
    // the proxy tighter than the ?token= shape it stands in for, and if it ever
    // regresses the token becomes readable from any script on the page.
    expect(JSON.stringify(body)).not.toContain(board.sessionToken);
    expect(body).not.toHaveProperty("session_token");
    // Never cached: `serve()` mints a new session on every board restart.
    expect(response.headers.get("cache-control")).toBe("no-store");
  }, 30000);

  it("lets the dashboard read the board through /api with no credential of its own", async () => {
    // Exactly what the browser does: same-origin, no Authorization header, no
    // cookie the page could have set — the host attaches the credential.
    const client = new BoardClient({ baseUrl: `${origin}/api`, projectId: board.projectId });
    const overview = await client.getOverview();
    expect(overview.project.id).toBe(board.projectId);
    expect(overview.counts).toBeDefined();
  }, 30000);

  it("lets the dashboard WRITE through /api, which is what needs the rewritten Origin", async () => {
    // A cookie-authenticated write is refused unless its Origin is one the
    // board allows, and the browser's Origin is this dev server, not the board.
    // If the host stopped rewriting it, this is the test that fails.
    const client = new BoardClient({
      baseUrl: `${origin}/api`,
      projectId: board.projectId,
      csrfToken: board.csrfToken,
    });
    const ticket = await client.createTicket({
      request_id: randomUUID(),
      title: "Created through the dev host",
      outcome: "The proxy carries the operator session",
      acceptance: [{ text: "it exists on the board" }],
    });
    expect(ticket.id).toMatch(/^DEMO-\d+$/);

    // And it is really on the board, not just echoed back by the proxy.
    const direct = new BoardClient({
      baseUrl: board.baseUrl,
      projectId: board.projectId,
      fetch: ((input: RequestInfo | URL, init: RequestInit = {}) => {
        const headers = new Headers(init.headers);
        headers.set("Cookie", `tb_session=${board.sessionToken}`);
        return globalThis.fetch(input, { ...init, headers });
      }) as typeof fetch,
    });
    const fetched = await direct.getTicket(ticket.id);
    expect(fetched.ticket.title).toBe("Created through the dev host");
  }, 30000);

  it("streams events through the proxy without buffering them", async () => {
    // An SSE response that a proxy buffers arrives when the stream ends, which
    // for an event stream is never — the dashboard would simply look dead.
    const response = await fetch(`${origin}/api/events`, {
      headers: { Accept: "text/event-stream", "X-Project-Id": board.projectId },
    });
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toMatch(/text\/event-stream/);

    const reader = response.body!.getReader();
    const first = await Promise.race([
      reader.read(),
      new Promise((_, reject) => setTimeout(() => reject(new Error("no bytes within 5s — buffered?")), 5000)),
    ]);
    expect((first as ReadableStreamReadResult<Uint8Array>).value!.length).toBeGreaterThan(0);
    await reader.cancel();
  }, 30000);

  it("answers a probe with no board session in a way that names the missing file", async () => {
    const lonely = await createServer({
      root: resolve(__dirname, "../../../ui"),
      configFile: false,
      logLevel: "silent",
      plugins: [boardHost({ stateDir: "/tmp/definitely-no-board-here-t184" })],
      server: { port: 0, host: "127.0.0.1" },
    });
    await lonely.listen();
    const address = lonely.httpServer!.address();
    const port = typeof address === "object" && address ? address.port : 0;

    const probe = await fetch(`http://127.0.0.1:${port}/__board/session`);
    // 200 with no project_id, not 404: "host up, board down" and "no host" are
    // different problems and the dashboard tells the operator different things.
    expect(probe.status).toBe(200);
    const body = (await probe.json()) as Record<string, string>;
    expect(body.project_id).toBeUndefined();
    expect(body.session_file).toContain("operator-session.json");

    const api = await fetch(`http://127.0.0.1:${port}/api/overview`, {
      headers: { "X-Project-Id": "prj_demo0001" },
    });
    expect(api.status).toBe(503);
    await lonely.close();
  }, 60000);
});
