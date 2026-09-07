import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import http from "node:http";
import type { Plugin, Connect } from "vite";

/**
 * Serves the dashboard on the same origin as the board API.
 *
 * The board server (T-180) publishes its API at the root path, hosts no static
 * files and sends no CORS headers, and `check_csrf` only accepts an `Origin` of
 * the board's own address. A vite dev server on :5173 therefore cannot talk to
 * it at all — not a same-origin cookie, not a cross-origin read, not a write.
 *
 * This plugin closes that gap from the `ui/` side rather than by adding a
 * static root to another lane's package:
 *
 *   GET /__board/session   -> { project_id, csrf_token, base_url: "/api" }
 *   ANY /api/*             -> the board, with `/api` stripped, the tb_session
 *                             cookie attached, and Origin rewritten to the
 *                             board's own base URL
 *
 * The session token stays in this process. The browser never receives it, so a
 * cross-site page cannot read it out of `document.cookie` and it cannot land in
 * a screenshot of the address bar — strictly tighter than the `?token=` shape
 * this stands in for. What the page does get is the CSRF token, which is meant
 * to be readable by the page it protects.
 *
 * This is a local operator convenience for a loopback V1, and it inherits that
 * threat model exactly: anyone who can reach this dev server is the operator,
 * because anyone who can read `operator-session.json` already was.
 */

export interface BoardHostOptions {
  /** Directory holding operator-session.json. Default: $BOARD_STATE_DIR or cwd. */
  stateDir?: string;
  /** Full path to the session file; wins over stateDir. Default: $BOARD_SESSION_FILE. */
  sessionFile?: string;
}

interface OperatorSession {
  project_id: string;
  session_token: string;
  csrf_token: string;
  url: string;
}

export const API_PREFIX = "/api";
export const SESSION_PROBE_PATH = "/__board/session";

function sessionPath(options: BoardHostOptions): string {
  if (options.sessionFile) return resolve(options.sessionFile);
  if (process.env.BOARD_SESSION_FILE) return resolve(process.env.BOARD_SESSION_FILE);
  const dir = options.stateDir ?? process.env.BOARD_STATE_DIR ?? process.cwd();
  return resolve(dir, "operator-session.json");
}

/**
 * Read on every request, never cached. `serve()` regenerates the file on each
 * start, so a cached copy means the dashboard keeps presenting a dead token
 * after a board restart and shows 401s that a reload would have fixed.
 */
function readSession(path: string): OperatorSession | null {
  if (!existsSync(path)) return null;
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8")) as OperatorSession;
    if (!parsed.project_id || !parsed.session_token || !parsed.url) return null;
    return parsed;
  } catch {
    return null;
  }
}

function sendJson(res: http.ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json");
  // The session probe must never be cached: it changes on every board restart.
  res.setHeader("Cache-Control", "no-store");
  res.end(payload);
}

function forward(session: OperatorSession, req: Connect.IncomingMessage, res: http.ServerResponse): void {
  const board = new URL(session.url);
  const path = (req.url ?? "/").slice(API_PREFIX.length) || "/";

  const headers: Record<string, string> = {
    Accept: (req.headers.accept as string) ?? "application/json",
    // Attached here, not in the browser. This is the whole point of the proxy.
    Cookie: `tb_session=${session.session_token}`,
    // check_csrf requires an allowed Origin on unsafe cookie-authenticated
    // writes, and the browser's Origin is this dev server. Rewrite it to the
    // board's own base URL, which is what the server allows.
    Origin: session.url,
    Host: board.host,
  };
  for (const name of [
    "content-type",
    // Content-Length is not optional politeness here. The board reads exactly
    // Content-Length bytes off the socket (httpd.py `_dispatch`), so without it
    // node falls back to chunked transfer-encoding, the board reads zero bytes
    // and answers "A JSON object body is required" — and, worse, the unread
    // chunked body stays in the socket, where HTTP/1.1 keep-alive makes the
    // *next* request on that connection parse it as garbage. One missing header
    // therefore breaks every write and then the request after it.
    "content-length",
    "x-project-id",
    "x-request-id",
    "x-csrf-token",
    "last-event-id",
  ]) {
    const value = req.headers[name];
    if (typeof value === "string") headers[name] = value;
  }

  const upstream = http.request(
    { hostname: board.hostname, port: board.port || 80, path, method: req.method, headers },
    (boardRes) => {
      res.statusCode = boardRes.statusCode ?? 502;
      for (const [key, value] of Object.entries(boardRes.headers)) {
        if (value !== undefined) res.setHeader(key, value as string | string[]);
      }
      // An event stream must not be buffered by anything in the middle, or the
      // dashboard sees nothing until the connection ends — which for SSE is
      // never. flushHeaders sends the response head before the first frame.
      if ((boardRes.headers["content-type"] ?? "").toString().startsWith("text/event-stream")) {
        res.setHeader("Cache-Control", "no-cache, no-transform");
        res.flushHeaders?.();
      }
      boardRes.pipe(res);
    },
  );

  upstream.on("error", (err) => {
    // The board being down is the single most common state this proxy sees, and
    // it has to be distinguishable from a board that answered badly. A 502 with
    // the reason lets the dashboard say "board unreachable" instead of guessing.
    if (!res.headersSent) {
      sendJson(res, 502, {
        error: {
          code: "network_error",
          status: 502,
          message: `The board at ${session.url} did not answer: ${err.message}`,
        },
      });
    } else {
      res.end();
    }
  });

  // Only pipe when there is a declared body. Piping a bodyless request would
  // make node choose chunked encoding for it, which is the same desync as
  // above; `end()` sends a clean bodyless request instead.
  if (headers["content-length"] && headers["content-length"] !== "0") {
    req.pipe(upstream);
  } else {
    upstream.end();
  }
}

export function boardHost(options: BoardHostOptions = {}): Plugin {
  const path = sessionPath(options);

  const middleware = (req: Connect.IncomingMessage, res: http.ServerResponse, next: Connect.NextFunction) => {
    const url = req.url ?? "/";
    const isProbe = url === SESSION_PROBE_PATH || url.startsWith(`${SESSION_PROBE_PATH}?`);
    const isApi = url === API_PREFIX || url.startsWith(`${API_PREFIX}/`);
    if (!isProbe && !isApi) return next();

    const session = readSession(path);
    if (!session) {
      if (isProbe) {
        // 200 with no project_id, not a 404: the difference between "no host"
        // and "host up, board down" is exactly what the dashboard needs to tell
        // the operator, and a 404 collapses the two.
        return sendJson(res, 200, { session_file: path });
      }
      return sendJson(res, 503, {
        error: {
          code: "network_error",
          status: 503,
          message: `No board session at ${path}. Start the board server first.`,
        },
      });
    }

    if (isProbe) {
      // Deliberately partial: project id and CSRF token only. The session token
      // is the credential and it stays in this process.
      return sendJson(res, 200, {
        project_id: session.project_id,
        csrf_token: session.csrf_token,
        base_url: API_PREFIX,
        board_url: session.url,
      });
    }

    return forward(session, req, res);
  };

  return {
    name: "ticket-board-host",
    configureServer(server) {
      server.middlewares.use(middleware);
    },
    configurePreviewServer(server) {
      server.middlewares.use(middleware);
    },
  };
}
