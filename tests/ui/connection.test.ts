import { describe, expect, it } from "vitest";
import { backoffDelayMs, displayedFreshness, INITIAL_CONNECTION } from "../../ui/src/state/connection";
import type { ConnectionState } from "../../ui/src/state/connection";
import type { StreamStatus } from "../../ui/src/types";

const stream = (state: StreamStatus["state"]): StreamStatus => ({
  state,
  snapshot_version: 1,
  as_of: "2026-09-06T14:32:00Z",
  last_event_id: "evt_000000000012_abcdef",
});

const connection = (overrides: Partial<ConnectionState> = {}): ConnectionState => ({
  ...INITIAL_CONNECTION,
  ...overrides,
});

describe("what the dashboard may claim about its own freshness", () => {
  it("says live only when the server says live AND this client is receiving events", () => {
    const result = displayedFreshness(stream("live"), connection({ phase: "live" }));
    expect(result.state).toBe("live");
    expect(result.downgraded).toBe(false);
  });

  it("downgrades the server's 'live' when this client's stream is down", () => {
    // The snapshot said live when it was written. That was true then. It is
    // not evidence about now, and this is the whole reason the module exists.
    const result = displayedFreshness(stream("live"), connection({ phase: "reconnecting" }));
    expect(result.state).toBe("reconnecting");
    expect(result.downgraded).toBe(true);
  });

  it("downgrades all the way to stale when the stream was refused outright", () => {
    const result = displayedFreshness(stream("live"), connection({ phase: "offline" }));
    expect(result.state).toBe("stale");
    expect(result.downgraded).toBe(true);
  });

  it("keeps the server's worse verdict even when this client's socket is fine", () => {
    // The board can be behind for reasons this client cannot see; a healthy
    // socket is not permission to overrule it.
    const result = displayedFreshness(stream("stale"), connection({ phase: "live" }));
    expect(result.state).toBe("stale");
    expect(result.downgraded).toBe(false);
  });

  it("treats never having heard from the board as stale, not as live", () => {
    const result = displayedFreshness(null, connection({ phase: "live" }));
    expect(result.state).toBe("stale");
    expect(result.asOf).toBeNull();
  });
});

describe("reconnect backoff", () => {
  it("never returns zero, at any attempt", () => {
    // A zero delay against a stream that ends immediately is an unbounded hot
    // loop: it starved a vitest worker to OOM on the T-203 review, and against
    // a live board it is a retry storm aimed at a server already in trouble.
    for (let attempt = 1; attempt <= 10; attempt += 1) {
      expect(backoffDelayMs(attempt, 1000, 30000, () => 0)).toBeGreaterThanOrEqual(1000);
    }
  });

  it("grows with the attempt number and stops at the cap", () => {
    const ceilingAt = (attempt: number) => backoffDelayMs(attempt, 1000, 30000, () => 1);
    expect(ceilingAt(1)).toBe(1000);
    expect(ceilingAt(2)).toBe(2000);
    expect(ceilingAt(3)).toBe(4000);
    expect(ceilingAt(20)).toBe(30000);
  });

  it("jitters within the window so two tabs that dropped together do not retry in lockstep", () => {
    const low = backoffDelayMs(5, 1000, 30000, () => 0);
    const high = backoffDelayMs(5, 1000, 30000, () => 1);
    expect(high).toBeGreaterThan(low);
    const middle = backoffDelayMs(5, 1000, 30000, () => 0.5);
    expect(middle).toBeGreaterThan(low);
    expect(middle).toBeLessThan(high);
  });
});
