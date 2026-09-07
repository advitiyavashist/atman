import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, readFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

/**
 * A real board server, on a real socket, for the duration of one test file.
 *
 * Everything in `tests/ui/api/` mocks `fetch` and replays fixtures. That is the
 * right shape for a client library and it is why T-203 could be trusted before
 * a server existed — but it cannot see anything that only happens when a server
 * is on the other end. This harness exists for exactly those: real cookies,
 * real CSRF refusals, a real SSE socket that can be cut, real idempotency keys
 * being spent, real 409s from two clients racing.
 *
 * It runs the same `serve()` entry point an operator runs, writes its state to
 * a throwaway directory, and takes an ephemeral port so parallel test files do
 * not collide.
 */

export interface LiveBoard {
  baseUrl: string;
  projectId: string;
  sessionToken: string;
  csrfToken: string;
  stateDir: string;
  stop(): Promise<void>;
}

const REPO_ROOT = resolve(__dirname, "../../..");

function waitFor<T>(probe: () => T | null, timeoutMs: number, what: string): Promise<T> {
  const started = Date.now();
  return new Promise((resolvePromise, reject) => {
    const tick = () => {
      let value: T | null = null;
      try {
        value = probe();
      } catch {
        value = null;
      }
      if (value !== null) return resolvePromise(value);
      if (Date.now() - started > timeoutMs) return reject(new Error(`timed out waiting for ${what}`));
      setTimeout(tick, 25);
    };
    tick();
  });
}

/**
 * Reserve a concrete free port before starting the board.
 *
 * Passing `port=0` and letting the OS choose is the obvious approach and it does
 * not work here, for a reason worth recording rather than working around
 * silently: `httpd.serve()` builds `base_url` from the port it was *asked* for,
 * before binding. With `port=0` the board therefore believes it lives at
 * `http://127.0.0.1:0` — it writes that URL into `operator-session.json`, and
 * its CSRF origin allowlist contains that URL and the two hardcoded :4319
 * defaults. Every operator write from the board's own dashboard is then refused
 * with `forbidden_scope: Origin ... is not allowed`. Reported to the board as a
 * T-180 defect; here we simply pick the port ourselves so the server is told
 * the truth.
 */
async function reserveFreePort(): Promise<number> {
  const net = await import("node:net");
  return new Promise((resolvePort, reject) => {
    const probe = net.createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      const port = typeof address === "object" && address ? address.port : 0;
      probe.close(() => resolvePort(port));
    });
  });
}

export async function startBoard(options: { port?: number; seedEvents?: number } = {}): Promise<LiveBoard> {
  const stateDir = mkdtempSync(join(tmpdir(), "t184-board-"));
  const port = options.port ?? (await reserveFreePort());
  // Seeding writes audit rows directly through the store rather than over HTTP.
  // The rows only have to exist and be counted — the point is to push the trail
  // head past the SSE retention window (1000) so a cursor can genuinely age
  // out, which no amount of API traffic in a test could reach affordably.
  const seed = options.seedEvents
    ? `
from ticket_board.storage.db import write_txn
with write_txn(httpd.board.store.conn) as conn:
    for i in range(${options.seedEvents}):
        httpd.board.store._audit(conn, creds["project_id"],
                                 {"type": "human", "id": "mem_seed", "display_name": "seed"},
                                 "ticket.updated", subject_type="ticket",
                                 subject_id="DEMO-0", summary="seed %d" % i)
`
    : "";
  const script = `
import json, sys
sys.path.insert(0, ${JSON.stringify(join(REPO_ROOT, "src"))})
from ticket_board.server import serve
httpd, thread, creds = serve(${JSON.stringify(join(stateDir, "board.sqlite3"))},
                             port=${port}, project_name="Demo",
                             state_dir=${JSON.stringify(stateDir)}, block=False)
${seed}
port = httpd.server_address[1]
print(json.dumps({"port": port, "project_id": creds["project_id"],
                  "session_token": creds["session_token"],
                  "csrf_token": creds["csrf_token"]}), flush=True)
import time
while True:
    time.sleep(3600)
`;
  const child: ChildProcess = spawn("python3", ["-c", script], { stdio: ["ignore", "pipe", "pipe"] });
  let stdout = "";
  let stderr = "";
  child.stdout?.on("data", (chunk) => (stdout += String(chunk)));
  child.stderr?.on("data", (chunk) => (stderr += String(chunk)));

  const line = await waitFor(
    () => {
      if (child.exitCode !== null) throw new Error(`board server exited ${child.exitCode}: ${stderr}`);
      const nl = stdout.indexOf("\n");
      return nl === -1 ? null : stdout.slice(0, nl);
    },
    20000,
    "the board server to report its port",
  );
  const info = JSON.parse(line) as {
    port: number;
    project_id: string;
    session_token: string;
    csrf_token: string;
  };

  return {
    baseUrl: `http://127.0.0.1:${info.port}`,
    projectId: info.project_id,
    sessionToken: info.session_token,
    csrfToken: info.csrf_token,
    stateDir,
    async stop() {
      child.kill("SIGKILL");
      await new Promise((r) => setTimeout(r, 20));
      rmSync(stateDir, { recursive: true, force: true });
    },
  };
}

/** The credential file `serve()` writes, read the way the dev host reads it. */
export function readOperatorSession(stateDir: string): Record<string, string> | null {
  const path = join(stateDir, "operator-session.json");
  if (!existsSync(path)) return null;
  return JSON.parse(readFileSync(path, "utf8")) as Record<string, string>;
}

/**
 * The dashboard's own client config against a live board.
 *
 * A browser would carry `tb_session` as a cookie; node's fetch has no cookie
 * jar, so the header is set explicitly. `credentials` is left off for the same
 * reason — it means nothing outside a browser, and setting it would suggest
 * this harness proves something about cookie handling that it does not.
 */
export function operatorConfig(board: LiveBoard) {
  return {
    baseUrl: board.baseUrl,
    projectId: board.projectId,
    csrfToken: board.csrfToken,
    fetch: ((input: RequestInfo | URL, init: RequestInit = {}) => {
      const headers = new Headers(init.headers);
      headers.set("Cookie", `tb_session=${board.sessionToken}`);
      // check_csrf refuses an unsafe cookie-authenticated request with no
      // Origin rather than giving it the benefit of the doubt, so a browserless
      // client has to present one the board allows.
      if ((init.method ?? "GET") !== "GET") headers.set("Origin", board.baseUrl);
      return globalThis.fetch(input, { ...init, headers });
    }) as typeof fetch,
  };
}

/** An agent's config: bearer token, no cookie, no CSRF. */
export function agentConfig(board: LiveBoard, token: string) {
  return { baseUrl: board.baseUrl, projectId: board.projectId, agentToken: token };
}
