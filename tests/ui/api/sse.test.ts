import { describe, expect, it, vi } from "vitest";
import { subscribeToEvents } from "../../../ui/src/api/sse";
import type { SnapshotRequiredEnvelope, StreamEnvelope } from "../../../ui/src/api/types";
import { loadFixture } from "./support/fixtures";
import { baseConfig } from "./support/mock-fetch";

function sseFrame(id: string, envelope: unknown): string {
  return `id: ${id}\ndata: ${JSON.stringify(envelope)}\n\n`;
}

function streamResponse(frames: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

/**
 * Every test below stops the subscriber the moment it has seen what it came
 * to check, from inside the handler that fires on it — not by counting
 * fetch calls after the fact. `reconnectDelayMs: 0` plus a mock that always
 * has a next frame ready means the read loop reconnects as fast as the
 * microtask queue allows; asserting call counts after some fixed wait would
 * be a race. Closing synchronously from the handler is not.
 */
function closeableHandlers() {
  let closeFn: (() => void) | undefined;
  return {
    bind(handle: { close(): void }) {
      closeFn = () => handle.close();
    },
    close() {
      closeFn?.();
    },
  };
}

describe("subscribeToEvents", () => {
  it("does not send Last-Event-ID on the very first connection", async () => {
    const heartbeat = loadFixture<StreamEnvelope>("events/heartbeat.json");
    const fetchMock = vi.fn(async () => streamResponse([sseFrame(heartbeat.event_id, heartbeat)]));
    const control = closeableHandlers();

    const handle = subscribeToEvents(
      baseConfig(fetchMock as unknown as typeof fetch),
      {
        onEvent: () => {},
        onSnapshotRequired: () => {},
        onHeartbeat: () => control.close(),
      },
      { reconnectDelayMs: 0 },
    );
    control.bind(handle);

    await handle.done;

    const [, firstInit] = fetchMock.mock.calls[0] as unknown as [unknown, RequestInit];
    expect(new Headers(firstInit.headers).has("Last-Event-ID")).toBe(false);
  });

  it("resumes with Last-Event-ID from the last event it saw after a reconnect", async () => {
    const ticketChanged = loadFixture<StreamEnvelope>("events/ticket-changed.json");
    const agentChanged = loadFixture<StreamEnvelope>("events/agent-changed.json");
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(async () => streamResponse([sseFrame(ticketChanged.event_id, ticketChanged)]))
      .mockImplementationOnce(async () => streamResponse([sseFrame(agentChanged.event_id, agentChanged)]));

    const events: StreamEnvelope[] = [];
    const control = closeableHandlers();
    const handle = subscribeToEvents(
      baseConfig(fetchMock as unknown as typeof fetch),
      {
        onEvent: (e) => {
          events.push(e);
          if (events.length === 2) control.close();
        },
        onSnapshotRequired: () => {},
      },
      { reconnectDelayMs: 0 },
    );
    control.bind(handle);

    await handle.done;

    expect(events.map((e) => e.event_id)).toEqual([ticketChanged.event_id, agentChanged.event_id]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [, firstInit] = fetchMock.mock.calls[0] as unknown as [unknown, RequestInit];
    const [, secondInit] = fetchMock.mock.calls[1] as unknown as [unknown, RequestInit];
    expect(new Headers(firstInit.headers).has("Last-Event-ID")).toBe(false);
    expect(new Headers(secondInit.headers).get("Last-Event-ID")).toBe(ticketChanged.event_id);
  });

  it("routes snapshot_required to its own handler instead of the ordinary event handler", async () => {
    const snapshotRequired = loadFixture<SnapshotRequiredEnvelope>("events/snapshot-required-cursor-expired.json");
    const fetchMock = vi.fn(async () => streamResponse([sseFrame(snapshotRequired.event_id, snapshotRequired)]));

    const ordinaryEvents: StreamEnvelope[] = [];
    const snapshotEvents: SnapshotRequiredEnvelope[] = [];
    const control = closeableHandlers();
    const handle = subscribeToEvents(
      baseConfig(fetchMock as unknown as typeof fetch),
      {
        onEvent: (e) => ordinaryEvents.push(e),
        onSnapshotRequired: (e) => {
          snapshotEvents.push(e);
          control.close();
        },
      },
      { reconnectDelayMs: 0 },
    );
    control.bind(handle);

    await handle.done;

    expect(snapshotEvents).toHaveLength(1);
    expect(ordinaryEvents).toHaveLength(0);
    expect(snapshotEvents[0].resume_hint.reason).toBe("cursor_expired");
  });

  it("resumes from an explicit initialLastEventId (e.g. restored after a page reload)", async () => {
    const heartbeat = loadFixture<StreamEnvelope>("events/heartbeat.json");
    const fetchMock = vi.fn(async () => streamResponse([sseFrame(heartbeat.event_id, heartbeat)]));
    const control = closeableHandlers();

    const handle = subscribeToEvents(
      baseConfig(fetchMock as unknown as typeof fetch),
      { onEvent: () => {}, onSnapshotRequired: () => {}, onHeartbeat: () => control.close() },
      { reconnectDelayMs: 0, initialLastEventId: "evt_000000000001_aaaaaa" },
    );
    control.bind(handle);

    await handle.done;

    const [, firstInit] = fetchMock.mock.calls[0] as unknown as [unknown, RequestInit];
    expect(new Headers(firstInit.headers).get("Last-Event-ID")).toBe("evt_000000000001_aaaaaa");
  });

  it("stops reconnecting once closed", async () => {
    const heartbeat = loadFixture<StreamEnvelope>("events/heartbeat.json");
    const fetchMock = vi.fn(async () => streamResponse([sseFrame(heartbeat.event_id, heartbeat)]));
    const control = closeableHandlers();

    const handle = subscribeToEvents(
      baseConfig(fetchMock as unknown as typeof fetch),
      { onEvent: () => {}, onSnapshotRequired: () => {}, onHeartbeat: () => control.close() },
      { reconnectDelayMs: 0 },
    );
    control.bind(handle);

    await handle.done;
    const callsAtClose = fetchMock.mock.calls.length;
    expect(callsAtClose).toBeGreaterThanOrEqual(1);

    // Give the loop a tick to prove it really stopped instead of racing ahead.
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(fetchMock.mock.calls.length).toBe(callsAtClose);
  });

  it("surfaces a non-2xx response as an error instead of silently stopping", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 401 }));
    const errors: unknown[] = [];
    const control = closeableHandlers();

    const handle = subscribeToEvents(
      baseConfig(fetchMock as unknown as typeof fetch),
      {
        onEvent: () => {},
        onSnapshotRequired: () => {},
        onError: (err) => {
          errors.push(err);
          control.close();
        },
      },
      { reconnectDelayMs: 0 },
    );
    control.bind(handle);

    await handle.done;

    expect(errors).toHaveLength(1);
    expect(errors[0]).toMatchObject({ code: "unexpected_status", status: 401 });
  });
});
