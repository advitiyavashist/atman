import { BoardError } from "./errors";
import type { ErrorEnvelope, ProjectId, RequestId } from "./types";

export interface BoardClientConfig {
  /** e.g. "http://127.0.0.1:4319" — loopback only for V1 (dependent-notes.md). */
  baseUrl: string;
  projectId: ProjectId;
  /**
   * Bearer token from a prior `exchangeEnrollment()` call. Omit for the
   * dashboard's own operator session, which authenticates via the HttpOnly
   * `tb_session` cookie instead — set `credentials` if the cookie needs a
   * non-default `RequestCredentials` mode, never put it in a query param.
   */
  agentToken?: string;
  credentials?: RequestCredentials;
  /** Unsafe operator-session methods require this to match the session's CSRF value. */
  csrfToken?: string;
  /** Injectable for tests; defaults to the ambient `fetch`. */
  fetch?: typeof fetch;
}

export type QueryValue = string | number | boolean | undefined;

export interface RequestOptions {
  method: "GET" | "POST" | "DELETE";
  path: string;
  query?: { [key: string]: QueryValue };
  body?: unknown;
  /** Single status or the set of statuses the contract declares as success for this call. */
  expectedStatus: number | number[];
  requestId?: RequestId;
  extraHeaders?: Record<string, string>;
}

function buildUrl(baseUrl: string, path: string, query?: Record<string, QueryValue>): string {
  const url = new URL(path, baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined) continue;
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

function isUnsafe(method: RequestOptions["method"]): boolean {
  return method === "POST" || method === "DELETE";
}

export function buildHeaders(config: BoardClientConfig, opts: RequestOptions): Headers {
  const headers = new Headers();
  headers.set("Accept", "application/json");
  headers.set("X-Project-Id", config.projectId);
  if (opts.body !== undefined) headers.set("Content-Type", "application/json");
  if (opts.requestId) headers.set("X-Request-Id", opts.requestId);
  if (config.agentToken) headers.set("Authorization", `Bearer ${config.agentToken}`);
  if (!config.agentToken && isUnsafe(opts.method) && config.csrfToken) {
    headers.set("X-CSRF-Token", config.csrfToken);
  }
  if (opts.extraHeaders) {
    for (const [key, value] of Object.entries(opts.extraHeaders)) headers.set(key, value);
  }
  return headers;
}

async function parseJsonSafely(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function looksLikeErrorEnvelope(value: unknown): value is ErrorEnvelope {
  return (
    typeof value === "object" &&
    value !== null &&
    "error" in value &&
    typeof (value as { error: unknown }).error === "object"
  );
}

/**
 * The one place every route method routes its wire call through: headers,
 * status-code contract check, and error-envelope-to-BoardError mapping all
 * live here so a route method is just "shape the URL and body, call this."
 */
export async function boardRequest<T>(config: BoardClientConfig, opts: RequestOptions): Promise<T> {
  const fetchImpl = config.fetch ?? globalThis.fetch;
  const url = buildUrl(config.baseUrl, opts.path, opts.query);
  const headers = buildHeaders(config, opts);
  const credentials = config.agentToken ? undefined : config.credentials ?? "include";

  let response: Response;
  try {
    response = await fetchImpl(url, {
      method: opts.method,
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      credentials,
    });
  } catch (cause) {
    throw new BoardError("network_error", 0, `Request to ${opts.path} failed before a response was received`, {
      details: { cause: cause instanceof Error ? cause.message : String(cause) },
    });
  }

  const parsed = await parseJsonSafely(response);

  if (!response.ok) {
    if (looksLikeErrorEnvelope(parsed)) throw BoardError.fromErrorEnvelope(parsed);
    throw new BoardError("unexpected_status", response.status, `${opts.path} returned ${response.status} with no parseable error body`);
  }

  const expected = Array.isArray(opts.expectedStatus) ? opts.expectedStatus : [opts.expectedStatus];
  if (!expected.includes(response.status)) {
    throw new BoardError(
      "unexpected_status",
      response.status,
      `${opts.path} returned ${response.status}, contract declares ${expected.join(" or ")} for this call`,
      { details: { expected, actual: response.status } },
    );
  }

  return parsed as T;
}
