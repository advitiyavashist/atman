import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { randomUUID } from "node:crypto";
import { BoardClient, subscribeToEvents, type StreamEnvelope } from "../../../ui/src/api";
import { operatorConfig, startBoard, type LiveBoard } from "./board-server";

/**
 * The event stream, over a real socket.
 *
 * `tests/ui/api/sse.test.ts` proves the frame parser against hand-built byte
 * sequences, which is the right way to test framing. What it cannot show is
 * whether this client and this server agree — that the board's `id:` lines are
 * the cursor the client resumes from, that `Last-Event-ID` actually replays,
 * and that a dropped connection recovers without the dashboard inventing
 * anything. Those need two processes and a socket between them.
 */
describe("live event stream", () => {
  let board: LiveBoard;
  let operator: BoardClient;

  beforeEach(async () => {
    board = await startBoard();
    operator = new BoardClient(operatorConfig(board));
  }, 30000);

  afterEach(async () => {
    await board?.stop();
  });

  function collect(options: { lastEventId?: string | null; onEvent?: (e: StreamEnvelope) => void } = {}) {
    const events: StreamEnvelope[] = [];
    const errors: unknown[] = [];
    let snapshotRequired: StreamEnvelope | null = null;
    const handle = subscribeToEvents(
      operatorConfig(board),
      {
        onEvent: (e) => {
          events.push(e);
          options.onEvent?.(e);
        },
        onSnapshotRequired: (e) => {
          snapshotRequired = e;
        },
        onError: (e) => errors.push(e),
      },
      // Non-zero: a zero delay against a stream that ends immediately hot-loops
      // hard enough to starve the timer queue (recorded on the T-203 review).
      { reconnectDelayMs: 50, initialLastEventId: options.lastEventId ?? null },
    );
    return { events, errors, handle, snapshot: () => snapshotRequired };
  }

  async function waitUntil(predicate: () => boolean, ms = 8000, what = "condition"): Promise<void> {
    const started = Date.now();
    while (!predicate()) {
      if (Date.now() - started > ms) throw new Error(`timed out waiting for ${what}`);
      await new Promise((r) => setTimeout(r, 25));
    }
  }

  it("delivers a ticket_changed frame to a live subscriber, with a cursor that advances", async () => {
    const stream = collect();
    // Give the subscription a moment to attach; a write that lands before the
    // stream is open is a replay case, not a live-delivery one, and conflating
    // them would make this test pass for the wrong reason.
    await new Promise((r) => setTimeout(r, 300));

    const ticket = await operator.createTicket({
      request_id: randomUUID(),
      title: "Stream me",
      outcome: "The dashboard learns about this without polling",
      acceptance: [{ text: "frame arrives" }],
    });

    await waitUntil(
      () => stream.events.some((e) => JSON.stringify(e).includes(ticket.id)),
      8000,
      "a frame mentioning the new ticket",
    );
    expect(stream.handle.lastEventId).toBeTruthy();
    expect(stream.errors).toEqual([]);
    stream.handle.close();
  }, 30000);

  it("replays what a disconnected tab missed when it resumes from its cursor", async () => {
    const first = collect();
    await new Promise((r) => setTimeout(r, 300));
    const before = await operator.createTicket({
      request_id: randomUUID(),
      title: "Before the drop",
      outcome: "seen live",
      acceptance: [{ text: "x" }],
    });
    await waitUntil(() => first.events.some((e) => JSON.stringify(e).includes(before.id)), 8000, "the first frame");
    const cursor = first.handle.lastEventId;
    first.handle.close();
    expect(cursor).toBeTruthy();

    // The tab is gone. Work continues on the board while nobody is watching —
    // this is the case the dashboard has to survive, not a contrived one.
    const missed = await operator.createTicket({
      request_id: randomUUID(),
      title: "While the tab was closed",
      outcome: "must still arrive",
      acceptance: [{ text: "x" }],
    });

    const resumed = collect({ lastEventId: cursor });
    await waitUntil(
      () => resumed.events.some((e) => JSON.stringify(e).includes(missed.id)),
      8000,
      "the missed frame to replay",
    );
    // And it must not re-deliver what the first subscription already saw.
    expect(resumed.events.some((e) => JSON.stringify(e).includes(before.id))).toBe(false);
    resumed.handle.close();
  }, 30000);

  it("treats an unparseable cursor as a gap rather than replaying the whole board", async () => {
    // A client that sends a cursor the server cannot read gets one
    // snapshot_required, not the entire trail from zero. Replaying everything
    // to a confused client is the more surprising of the two failures, and the
    // dashboard's answer to either is the same: re-read every screen.
    const stream = collect({ lastEventId: "not-an-event-id" });
    await waitUntil(() => stream.snapshot() !== null, 8000, "a snapshot_required frame");
    const envelope = stream.snapshot()! as StreamEnvelope & { resume_hint?: { reason?: string } };
    expect(envelope.type).toBe("snapshot_required");
    expect(envelope.resume_hint?.reason).toBe("cursor_expired");
    stream.handle.close();
  }, 30000);

  it("stops retrying a stream the board refuses for a reason retrying cannot fix", async () => {
    const errors: unknown[] = [];
    let connects = 0;
    const handle = subscribeToEvents(
      {
        ...operatorConfig(board),
        // A project this session has no scope for is 403 forbidden_scope, and
        // it will be 403 forever. Retrying it is an unbounded loop against a
        // server that has already answered.
        projectId: "prj_notarealproject",
        fetch: ((input: RequestInfo | URL, init: RequestInit = {}) => {
          connects += 1;
          return operatorConfig(board).fetch!(input, init);
        }) as typeof fetch,
      },
      { onEvent: () => {}, onSnapshotRequired: () => {}, onError: (e) => errors.push(e) },
      { reconnectDelayMs: 50 },
    );

    await handle.done;
    expect(errors.length).toBeGreaterThan(0);
    // Exactly one attempt: the refusal is terminal, so there is no second.
    expect(connects).toBe(1);
  }, 30000);
});

/**
 * The genuine replay gap: a cursor that has fallen out of the retention window.
 *
 * This needs its own board because the window is 1000 events wide and expiry is
 * measured from the head (`requested < head - retention`), not from zero — so on
 * a small board no cursor can ever expire, and a test that asserted otherwise
 * would be asserting something the server never does. The trail is seeded past
 * the window through the store, which is the only affordable way to reach the
 * condition.
 */
describe("a cursor that has aged out of the retention window", () => {
  let aged: LiveBoard;

  beforeEach(async () => {
    aged = await startBoard({ seedEvents: 1200 });
  }, 30000);

  afterEach(async () => {
    await aged?.stop();
  });

  it("answers one snapshot_required with reason cursor_expired", async () => {
    let snapshotRequired: (StreamEnvelope & { resume_hint?: { reason?: string } }) | null = null;
    const events: StreamEnvelope[] = [];
    const handle = subscribeToEvents(
      operatorConfig(aged),
      {
        onEvent: (e) => events.push(e),
        onSnapshotRequired: (e) => {
          snapshotRequired = e as StreamEnvelope & { resume_hint?: { reason?: string } };
        },
        onError: () => {},
      },
      // Well inside the seeded 1200, so this is a real aged-out cursor and not
      // an unparseable one taking the same path.
      { reconnectDelayMs: 50, initialLastEventId: "evt_000000000005_aaaaaa" },
    );

    const started = Date.now();
    while (snapshotRequired === null) {
      if (Date.now() - started > 8000) throw new Error("timed out waiting for snapshot_required");
      await new Promise((r) => setTimeout(r, 25));
    }
    const envelope = snapshotRequired as StreamEnvelope & { resume_hint?: { reason?: string } };
    expect(envelope.type).toBe("snapshot_required");
    expect(envelope.resume_hint?.reason).toBe("cursor_expired");
    handle.close();
  }, 30000);
});
