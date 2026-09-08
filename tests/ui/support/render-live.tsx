import { render, type RenderResult } from "@testing-library/react";
import type { ReactNode } from "react";
import { vi } from "vitest";
import { BoardProvider } from "../../../ui/src/state/BoardProvider";
import type { BoardSession } from "../../../ui/src/session";
import { matchPost201RequestSchema, validatePostRequestBody } from "./openapi-request-validate";

/**
 * Mount a screen against a mocked board, not against fixtures on disk.
 *
 * T-183's screens read a fixture module directly, so its tests could render
 * them with no server at all. Since T-184 every screen reads the API, so a
 * screen test has to answer HTTP — and answering it here (rather than importing
 * the fixture the screen used to import) is the point: the test now exercises
 * the same request/response path the browser takes, including the client's own
 * response validation, and a screen that stopped calling the right route fails
 * rather than quietly rendering a stale import.
 *
 * Payloads still come from `ui/src/fixtures/data/`, which
 * `tests/ui/fixtures-parity.test.ts` keeps byte-identical to the frozen
 * contract's `tests/fixtures/`. So these are contract-exact shapes, not shapes
 * a test author invented.
 */

export interface RouteMap {
  /** Keyed by "METHOD /path", e.g. "GET /overview". */
  [route: string]: unknown;
}

export const TEST_SESSION: BoardSession = {
  source: "hosted",
  boardLabel: "/api",
  config: {
    baseUrl: "http://127.0.0.1:4319",
    projectId: "prj_demo0001",
    csrfToken: "csrf-for-tests",
  },
};

export interface LiveHarness {
  fetchMock: ReturnType<typeof vi.fn>;
  /** Every non-stream request the screen made, in order. */
  calls(): { method: string; path: string; body: unknown }[];
}

function malformedRequestResponse(message: string): Response {
  return new Response(
    JSON.stringify({
      error: { code: "malformed_request", status: 400, message },
    }),
    { status: 400, headers: { "Content-Type": "application/json" } },
  );
}

export const CREATED_ROUTES = [
  "/updates",
  "/reviews",
  "/tickets",
  "/enrollments",
  "/sessions",
  "/assignments",
  "/invitations",
  "/invitations/exchange",
  "/channels",
  "/messages",
  "/task",
  "/members",
];

/**
 * Build a fetch that serves `routes` and holds `/events` open.
 *
 * The stream is deliberately a never-ending body rather than one that closes
 * immediately: a stream that ends instantly makes the provider reconnect in a
 * loop, which in a jsdom test is a busy loop competing with the assertions.
 * Holding it open is also what a real board does.
 */
export function boardFetch(routes: RouteMap, options: { streamStatus?: number } = {}): LiveHarness {
  const calls: { method: string; path: string; body: unknown }[] = [];

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(input.toString());
    const method = (init.method ?? "GET").toUpperCase();
    const path = url.pathname;
    const body = init.body ? JSON.parse(init.body as string) : undefined;

    if (path.endsWith("/events")) {
      const status = options.streamStatus ?? 200;
      if (status !== 200) {
        return new Response(
          JSON.stringify({ error: { code: "forbidden_scope", status, message: "no stream for you" } }),
          { status, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(new ReadableStream({ start() {} }), {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    }

    calls.push({ method, path, body });
    const key = `${method} ${path}`;
    const handler = routes[key];
    if (handler === undefined) {
      return new Response(
        JSON.stringify({ error: { code: "not_found", status: 404, message: `No test route for ${key}` } }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      );
    }
    const post201Schema = method === "POST" ? matchPost201RequestSchema(path) : null;
    if (post201Schema !== null) {
      if (body === undefined) {
        return malformedRequestResponse("Request body is required");
      }
      const schemaErrors = validatePostRequestBody(post201Schema, body);
      if (schemaErrors.length > 0) {
        return malformedRequestResponse(schemaErrors.join("; "));
      }
    }

    const resolved = typeof handler === "function" ? (handler as (r: unknown) => unknown)({ body, url }) : handler;
    if (resolved instanceof Response) return resolved;
    const status = post201Schema !== null ? 201 : 200;
    return new Response(JSON.stringify(resolved), { status, headers: { "Content-Type": "application/json" } });
  });

  return { fetchMock: fetchMock as unknown as ReturnType<typeof vi.fn>, calls: () => calls };
}

export function renderLive(
  ui: ReactNode,
  harness: LiveHarness,
  options: { reconnectDelayMs?: number } = {},
): RenderResult {
  const session: BoardSession = {
    ...TEST_SESSION,
    config: { ...TEST_SESSION.config, fetch: harness.fetchMock as unknown as typeof fetch },
  };
  return render(
    <BoardProvider session={session} reconnectDelayMs={options.reconnectDelayMs ?? 50_000}>
      {ui}
    </BoardProvider>,
  );
}
