import { vi } from "vitest";
import type { BoardClientConfig } from "../../../../ui/src/api/http";

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function baseConfig(fetchImpl: typeof fetch, overrides: Partial<BoardClientConfig> = {}): BoardClientConfig {
  return {
    baseUrl: "http://127.0.0.1:4319",
    projectId: "prj_demo0001",
    fetch: fetchImpl,
    ...overrides,
  };
}

/** A fetch mock that returns one Response per call, in order, and records every call. */
export function sequenceFetch(responses: Response[]): ReturnType<typeof vi.fn> {
  const fn = vi.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>();
  for (const response of responses) fn.mockImplementationOnce(async () => response);
  return fn;
}

export function lastCallHeaders(fetchMock: ReturnType<typeof vi.fn>): Headers {
  const [, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1] as [RequestInfo | URL, RequestInit];
  return new Headers(init?.headers);
}

export function lastCallBody(fetchMock: ReturnType<typeof vi.fn>): unknown {
  const [, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1] as [RequestInfo | URL, RequestInit];
  return init?.body ? JSON.parse(init.body as string) : undefined;
}

export function lastCallUrl(fetchMock: ReturnType<typeof vi.fn>): string {
  const [url] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1] as [RequestInfo | URL, RequestInit];
  return url.toString();
}
