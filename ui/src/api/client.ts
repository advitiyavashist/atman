/**
 * The one place this app talks to `atm ui`.
 *
 * Two deployments, one client:
 *
 * 1. **The served bundle** (`atm ui`, then `/app/`). The page is same-origin
 *    with the API and carries `<meta name="atman-api">` and
 *    `<meta name="atman-token">`, which the server inserts. Writes work,
 *    including `POST /msg`, because Origin equals Host.
 * 2. **The dev server** (`npm run dev -w ui` beside
 *    `atm ui --dev-origin http://localhost:5173`). There is no meta tag, so
 *    the base comes from `?api=`, `VITE_ATMAN_API` or the documented default
 *    `http://127.0.0.1:8765`, and the write token comes from
 *    `GET /api/v1/session` with `X-Atman-Client: app` (the custom header
 *    forces a preflight only an allowed origin passes).
 *
 * No origin is ever accepted that is not loopback: `apiBase` refuses anything
 * else rather than quietly reaching out to a host the operator did not name.
 */

import { SCHEMA_OF, type RouteName } from "./types";
import type {
  Board,
  Lead,
  NeedsYou,
  Plan,
  Projects,
  Session,
  Thread,
  Ticket,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly url: string;
  constructor(message: string, status: number, url: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.url = url;
  }
}

const LOOPBACK = new Set(["127.0.0.1", "localhost", "::1", "[::1]", "0:0:0:0:0:0:0:1"]);

/** true for an http(s) origin on a loopback name. Nothing else may be an API base. */
export function isLoopbackOrigin(value: string): boolean {
  let u: URL;
  try {
    u = new URL(value);
  } catch {
    return false;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return false;
  return LOOPBACK.has(u.hostname.toLowerCase());
}

function metaContent(name: string): string {
  if (typeof document === "undefined") return "";
  const el = document.querySelector(`meta[name="${name}"]`);
  return (el?.getAttribute("content") || "").trim();
}

export interface Origin {
  /** e.g. "http://127.0.0.1:8765/api/v1" — no trailing slash. */
  api: string;
  /** e.g. "http://127.0.0.1:8765" — where POST /msg lives. */
  server: string;
  /** true when the page is served by atm ui itself (token in a meta tag). */
  sameOrigin: boolean;
  /** Set when the configured base was refused, so the screen can say why. */
  refused?: string;
}

const DEV_DEFAULT = "http://127.0.0.1:8765";

/**
 * Where the API is, and whether we are same-origin with it.
 *
 * `?api=` and `VITE_ATMAN_API` take a server origin, not a path: the API
 * always lives under `/api/v1` on that origin.
 */
export function resolveOrigin(search = typeof location === "undefined" ? "" : location.search): Origin {
  const metaApi = metaContent("atman-api");
  if (metaApi) {
    const base = metaApi.replace(/\/+$/, "");
    const server = typeof location === "undefined" ? "" : location.origin;
    return { api: base.startsWith("http") ? base : `${server}${base}`, server, sameOrigin: true };
  }
  const fromQuery = new URLSearchParams(search).get("api") || "";
  const fromEnv = (import.meta.env?.VITE_ATMAN_API as string | undefined) || "";
  const wanted = (fromQuery || fromEnv || DEV_DEFAULT).trim().replace(/\/+$/, "");
  if (!isLoopbackOrigin(wanted)) {
    return {
      api: "",
      server: "",
      sameOrigin: false,
      refused: `${wanted} is not a loopback origin; this app talks to atm ui on localhost only`,
    };
  }
  return { api: `${wanted}/api/v1`, server: wanted, sameOrigin: false };
}

/** Dev-only response check against the real JSON Schema. Never bundled in a production build. */
async function devCheck(route: RouteName, body: unknown, url: string): Promise<void> {
  if (!import.meta.env.DEV) return;
  const mod = await import("./devCheck");
  mod.checkAgainstSchema(SCHEMA_OF[route], body, url);
}

export class AtmanApi {
  readonly origin: Origin;
  private token = "";
  private readonly fetchImpl: typeof fetch;

  constructor(origin: Origin = resolveOrigin(), fetchImpl?: typeof fetch) {
    this.origin = origin;
    this.fetchImpl = fetchImpl ?? ((...a: Parameters<typeof fetch>) => fetch(...a));
    if (origin.sameOrigin) this.token = metaContent("atman-token");
  }

  /** The write token, or "" when this deployment has none yet. */
  get writeToken(): string {
    return this.token;
  }

  private url(path: string, params?: Record<string, string | number | undefined>): string {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params || {})) {
      if (v === undefined || v === "") continue;
      q.set(k, String(v));
    }
    const qs = q.toString();
    return `${this.origin.api}${path}${qs ? `?${qs}` : ""}`;
  }

  private async get<T>(route: RouteName, path: string, params?: Record<string, string | number | undefined>): Promise<T> {
    if (this.origin.refused) throw new ApiError(this.origin.refused, 0, path);
    const url = this.url(path, params);
    let res: Response;
    try {
      res = await this.fetchImpl(url, {
        method: "GET",
        headers: { Accept: "application/json", "X-Atman-Client": "app" },
      });
    } catch (cause) {
      throw new ApiError(
        `no answer from ${this.origin.server} — is atm ui running? (${cause instanceof Error ? cause.message : String(cause)})`,
        0,
        url,
      );
    }
    const text = await res.text();
    let body: unknown;
    try {
      body = text ? JSON.parse(text) : undefined;
    } catch {
      throw new ApiError(`${url} answered ${res.status} with a body that is not JSON`, res.status, url);
    }
    if (!res.ok) {
      const msg = (body as { error?: string } | undefined)?.error || `${res.status}`;
      throw new ApiError(msg, res.status, url);
    }
    await devCheck(route, body, url);
    return body as T;
  }

  /** GET /api/v1/session — also how the dev server gets the write token. */
  async session(): Promise<Session> {
    const s = await this.get<Session>("session", "/session");
    if (s.token) this.token = s.token;
    return s;
  }

  projects(): Promise<Projects> {
    return this.get<Projects>("projects", "/projects");
  }

  board(project?: string, seat?: string): Promise<Board> {
    return this.get<Board>("board", "/board", { project, seat });
  }

  plan(project?: string): Promise<Plan> {
    return this.get<Plan>("plan", "/plan", { project });
  }

  lead(project?: string): Promise<Lead> {
    return this.get<Lead>("lead", "/lead", { project });
  }

  thread(opts: { project?: string; with?: string; before?: string; limit?: number; all?: boolean }): Promise<Thread> {
    return this.get<Thread>("thread", "/thread", {
      project: opts.project,
      with: opts.with,
      before: opts.before,
      limit: opts.limit,
      all: opts.all ? "1" : undefined,
    });
  }

  ticket(id: string, project?: string): Promise<Ticket> {
    return this.get<Ticket>("ticket", `/ticket/${encodeURIComponent(id)}`, { project });
  }

  needsYou(project?: string): Promise<NeedsYou> {
    return this.get<NeedsYou>("needs-you", "/needs-you", { project });
  }

  /**
   * POST /api/v1/lead {seat} — the operator picks this project's lead.
   *
   * The same write as `atm lead set <seat>`. It needs the operator and the
   * launch token; until T-1104 adds `--operator` the server answers 400 and
   * the picker shows the command instead.
   */
  async setLead(seat: string, project?: string): Promise<{ ok: boolean; lead: string; harness: string; capability: string; project: string }> {
    const url = `${this.origin.api}/lead`;
    const res = await this.fetchImpl(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Atman-Token": this.token,
        "X-Atman-Client": "app",
      },
      body: JSON.stringify(project ? { seat, project } : { seat }),
    });
    const text = await res.text();
    let parsed: { ok?: boolean; error?: string } = {};
    try {
      parsed = text ? JSON.parse(text) : {};
    } catch {
      throw new ApiError(`POST /api/v1/lead answered ${res.status} with a body that is not JSON`, res.status, url);
    }
    if (!res.ok || !parsed.ok) throw new ApiError(parsed.error || `${res.status}`, res.status, url);
    return parsed as { ok: boolean; lead: string; harness: string; capability: string; project: string };
  }

  /**
   * POST /msg — the embedded composer's route, which is where a post goes
   * (the API adds no message route; see docs/api/app-v2.md).
   *
   * `from` is the operator. Until T-1104 hardens the route the server still
   * reads it from the payload, so the app sends the operator it was told and
   * nothing else. With no operator the caller must not get here: the composer
   * shows the command to set one instead.
   */
  async postMessage(body: { from: string; text: string; to?: string; re?: string; kind?: "message" | "task" }): Promise<{ ok: boolean; posted?: unknown }> {
    if (!this.token) {
      throw new ApiError(
        "no write token: open the app that atm ui serves at /app/, or start atm ui --dev-origin <this origin>",
        0,
        "/msg",
      );
    }
    const url = `${this.origin.server}/msg`;
    let res: Response;
    try {
      res = await this.fetchImpl(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Atman-Token": this.token },
        body: JSON.stringify(body),
      });
    } catch (cause) {
      throw new ApiError(
        this.origin.sameOrigin
          ? `POST /msg never answered (${cause instanceof Error ? cause.message : String(cause)})`
          : "POST /msg is same-origin only: it sends no CORS headers and refuses an Origin that is not its Host. " +
            "Post from the bundle atm ui serves at /app/.",
        0,
        url,
      );
    }
    const text = await res.text();
    let parsed: { ok?: boolean; error?: string; posted?: unknown } = {};
    try {
      parsed = text ? JSON.parse(text) : {};
    } catch {
      throw new ApiError(`POST /msg answered ${res.status} with a body that is not JSON`, res.status, url);
    }
    if (!res.ok || !parsed.ok) throw new ApiError(parsed.error || `POST /msg answered ${res.status}`, res.status, url);
    return { ok: true, posted: parsed.posted };
  }
}
