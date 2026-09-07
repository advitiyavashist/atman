import type { StreamStatus } from "../types";

/**
 * What the dashboard is allowed to claim about its own freshness.
 *
 * Two facts disagree constantly and only one of them is trustworthy at a time.
 * The server puts a `stream` block on every screen response saying how fresh
 * *it* thinks the board is; the client knows whether its own event stream is
 * actually connected. A snapshot fetched three minutes ago said "live" when it
 * was written and is still saying it now.
 *
 * So the displayed state is the WORSE of the two, never the server's alone.
 * That is the whole rule, and it is the reason this module exists rather than
 * a boolean on the provider.
 */

export type ConnectionPhase =
  /** No credential resolved — we have never been connected and cannot be. */
  | "unconfigured"
  /** First connect in flight. */
  | "connecting"
  /** Stream open, frames arriving. */
  | "live"
  /** Stream dropped, a retry is scheduled. Data on screen is last-known. */
  | "reconnecting"
  /** Stream refused in a way retrying cannot fix (401/403/404). */
  | "offline";

export interface ConnectionState {
  phase: ConnectionPhase;
  /** When this client last received any frame, heartbeat included. */
  lastEventAt: string | null;
  /** Failed connect attempts since the last successful frame. */
  attempts: number;
  /** Epoch ms of the next scheduled retry, for a live countdown. */
  nextRetryAt: number | null;
  lastError: string | null;
}

export const INITIAL_CONNECTION: ConnectionState = {
  phase: "connecting",
  lastEventAt: null,
  attempts: 0,
  nextRetryAt: null,
  lastError: null,
};

/**
 * Ranked best-to-worst. The frozen `StreamStatus.state` enum is exactly
 * `live | reconnecting | stale` (docs/contracts/openapi.yaml) — there is no
 * "disconnected" member, and `stale` is the one whose copy reads "Connection
 * lost", so it is the worst of the three, not the middle.
 */
const RANK: Record<StreamStatus["state"], number> = { live: 0, reconnecting: 1, stale: 2 };

export interface DisplayedFreshness {
  state: StreamStatus["state"];
  /** The server's own timestamp for the snapshot on screen. */
  asOf: string | null;
  /** True when the client downgraded the server's claim. */
  downgraded: boolean;
}

/**
 * Combine the server's stream claim with what this client actually observes.
 *
 * `serverStream` may be null before the first successful read — that is
 * "disconnected", not "live", because we have never heard from the board.
 */
export function displayedFreshness(
  serverStream: StreamStatus | null,
  connection: ConnectionState,
): DisplayedFreshness {
  const clientState: StreamStatus["state"] =
    connection.phase === "live"
      ? "live"
      : connection.phase === "connecting" || connection.phase === "reconnecting"
        ? "reconnecting"
        : "stale";
  // No snapshot yet means we have never heard from the board. That is the worst
  // state, not the best — defaulting it to "live" is precisely the optimistic
  // claim this module exists to prevent.
  const serverState = serverStream?.state ?? "stale";
  const worst = RANK[clientState] >= RANK[serverState] ? clientState : serverState;
  return {
    state: worst,
    asOf: serverStream?.as_of ?? null,
    downgraded: RANK[clientState] > RANK[serverState],
  };
}

/**
 * Exponential backoff with full jitter, capped.
 *
 * `reconnectDelayMs: 0` — what the T-203 tests use — hot-loops fast enough
 * against a stream that ends immediately to starve the timer queue and OOM a
 * vitest worker (recorded by cos-opus on the T-203 review). Against a real
 * board it is worse: an unbounded retry rate aimed at a server that is already
 * struggling. Jitter matters because two dashboard tabs that dropped together
 * would otherwise retry in lockstep forever.
 */
export function backoffDelayMs(attempt: number, base = 1000, cap = 30000, random = Math.random): number {
  const ceiling = Math.min(cap, base * 2 ** Math.max(0, attempt - 1));
  return Math.round(random() * (ceiling - base) + base);
}
