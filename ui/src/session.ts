import type { BoardClientConfig } from "./api";

/**
 * Where the dashboard's credentials come from.
 *
 * The frozen contract has no operator sign-in route (docs/api-notes.md, "Two
 * contract gaps"), so a session is bootstrapped out of band. The planner's
 * 12:50Z ruling described a one-time `?token=` on a UI root that exchanges it
 * for a `tb_session` cookie — but the merged server hosts no static files, has
 * no UI root to hang that exchange on, and sends no CORS headers, so a
 * dashboard served from any other origin cannot reach the API at all. Building
 * that root would mean editing `src/ticket_board/server/`, which belongs to
 * another lane.
 *
 * So this build resolves the session in three ways, most-preferred first, and
 * the preferred one keeps the token further from the page than the ruling did:
 *
 *  1. `HOSTED` — a same-origin host (the vite dev/preview plugin in
 *     `ui/board-host.ts`, or any reverse proxy shaped like it) answers
 *     `GET /__board/session` with the project id and CSRF token, and forwards
 *     `/api/*` to the board with the `tb_session` cookie attached itself. The
 *     session token never reaches JavaScript.
 *  2. `INJECTED` — a host that renders the page can set
 *     `window.__BOARD_SESSION__` directly.
 *  3. `URL_TOKEN` — the notebook pattern, kept as the fallback for whoever
 *     builds the static root later: `?token=&project=&csrf=` is read once,
 *     written to `document.cookie`, and stripped from the address bar. Only
 *     usable when the page is already same-origin with the board, because the
 *     cookie cannot be set for another origin and a cross-origin request would
 *     be refused for want of CORS.
 *
 * Nothing here invents a credential. If none of the three is present the app
 * says so and offers no board data, rather than rendering an empty board that
 * looks like a quiet one.
 */

export type SessionSource = "hosted" | "injected" | "url-token";

export interface BoardSession {
  config: BoardClientConfig;
  source: SessionSource;
  /** Shown in the footer so an operator can tell which board they are looking at. */
  boardLabel: string;
}

export type SessionResolution =
  | { status: "ready"; session: BoardSession }
  | { status: "unconfigured"; reason: string };

interface InjectedSession {
  project_id: string;
  csrf_token?: string;
  base_url?: string;
}

declare global {
  interface Window {
    __BOARD_SESSION__?: InjectedSession;
  }
}

/** The path the host plugin answers. Same-origin by construction. */
export const SESSION_PROBE_PATH = "/__board/session";
/** Everything under here is forwarded to the board by the host. */
export const HOSTED_API_BASE = "/api";

function stripTokenFromUrl(): void {
  try {
    const url = new URL(window.location.href);
    let touched = false;
    for (const key of ["token", "project", "csrf"]) {
      if (url.searchParams.has(key)) {
        url.searchParams.delete(key);
        touched = true;
      }
    }
    // A session token in the address bar survives in history, in a screenshot
    // and in whatever the next page sends as Referer. Take it out of the URL
    // the moment it has been read.
    if (touched) window.history.replaceState({}, "", url.toString());
  } catch {
    // A non-URL location (about:blank in a test harness) has nothing to strip.
  }
}

function fromUrlToken(): SessionResolution | null {
  let params: URLSearchParams;
  try {
    params = new URL(window.location.href).searchParams;
  } catch {
    return null;
  }
  const token = params.get("token");
  const projectId = params.get("project");
  if (!token || !projectId) return null;
  const csrf = params.get("csrf") ?? undefined;
  try {
    // Session cookie, not persistent: closing the tab should not leave an
    // operator session sitting on disk. Path=/ so both the API routes and the
    // page share it.
    document.cookie = `tb_session=${token}; path=/; SameSite=Strict`;
  } catch {
    return { status: "unconfigured", reason: "This browser refused to store the session cookie, so the board cannot be reached." };
  }
  stripTokenFromUrl();
  return {
    status: "ready",
    session: {
      source: "url-token",
      boardLabel: window.location.origin,
      config: { baseUrl: window.location.origin, projectId, csrfToken: csrf, credentials: "include" },
    },
  };
}

function fromInjected(): SessionResolution | null {
  const injected = typeof window !== "undefined" ? window.__BOARD_SESSION__ : undefined;
  if (!injected?.project_id) return null;
  const baseUrl = injected.base_url ?? HOSTED_API_BASE;
  return {
    status: "ready",
    session: {
      source: "injected",
      boardLabel: baseUrl,
      config: {
        baseUrl: new URL(baseUrl, window.location.origin).toString(),
        projectId: injected.project_id,
        csrfToken: injected.csrf_token,
        credentials: "include",
      },
    },
  };
}

async function fromHost(fetchImpl: typeof fetch): Promise<SessionResolution | null> {
  let response: Response;
  try {
    response = await fetchImpl(SESSION_PROBE_PATH, { headers: { Accept: "application/json" } });
  } catch {
    return null;
  }
  if (!response.ok) return null;
  let body: InjectedSession;
  try {
    body = (await response.json()) as InjectedSession;
  } catch {
    return null;
  }
  if (!body?.project_id) {
    return {
      status: "unconfigured",
      // The host is running but could not find a board — that is a different
      // problem from "no host", and saying so saves the operator a hunt.
      reason: "The dashboard host is running but has no board session. Start the board server, then reload.",
    };
  }
  const baseUrl = body.base_url ?? HOSTED_API_BASE;
  return {
    status: "ready",
    session: {
      source: "hosted",
      boardLabel: baseUrl,
      config: {
        baseUrl: new URL(baseUrl, window.location.origin).toString(),
        projectId: body.project_id,
        csrfToken: body.csrf_token,
        credentials: "include",
      },
    },
  };
}

export async function resolveSession(fetchImpl: typeof fetch = globalThis.fetch): Promise<SessionResolution> {
  // Order matters: a URL token is an explicit act by whoever opened the link,
  // so it wins over a host default, and the injected form is for a host that
  // renders the page itself.
  const url = fromUrlToken();
  if (url) return url;
  const injected = fromInjected();
  if (injected) return injected;
  const hosted = await fromHost(fetchImpl);
  if (hosted) return hosted;
  return {
    status: "unconfigured",
    reason:
      "No board session. Start the board server, then run the dashboard through its host so it can read operator-session.json — see ui/README.md.",
  };
}
