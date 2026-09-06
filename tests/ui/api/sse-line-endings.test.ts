/** cos-opus T-203 second-review attack. Place at tests/ui/api/.
 *  reconnectDelayMs must be NON-zero: with 0, an immediately-ending 200 stream
 *  hot-loops hard enough to starve the timer queue and OOM the vitest worker. */
import { describe, expect, it, vi } from "vitest";
import { subscribeToEvents } from "../../../ui/src/api/sse";
import type { StreamEnvelope } from "../../../ui/src/api/types";
import { loadFixture } from "./support/fixtures";
import { baseConfig } from "./support/mock-fetch";

function once(chunks: string[]) {
  // Serve the chunks once; every later attempt hangs, so a stall reads as
  // "never delivered" rather than as a reconnect storm.
  let served = false;
  return vi.fn(async () => {
    if (served) return new Response(new ReadableStream<Uint8Array>({}), { status: 200 });
    served = true;
    const enc = new TextEncoder();
    return new Response(new ReadableStream<Uint8Array>({
      start(c) { for (const s of chunks) c.enqueue(enc.encode(s)); c.close(); },
    }), { status: 200, headers: { "Content-Type": "text/event-stream" } });
  });
}

async function delivered(chunks: string[]): Promise<boolean> {
  let hit = false;
  const handle = subscribeToEvents(
    baseConfig(once(chunks) as unknown as typeof fetch),
    { onEvent: () => { hit = true; }, onSnapshotRequired: () => { hit = true; },
      onHeartbeat: () => { hit = true; }, onError: () => {} },
    { reconnectDelayMs: 200 },
  );
  await new Promise((r) => setTimeout(r, 800));
  handle.close();
  return hit;
}

describe("SSE frame boundaries: every legal terminator pair", () => {
  const boundaries: Array<[string, string]> = [
    ["LF LF", "\n\n"],
    ["CRLF CRLF", "\r\n\r\n"],
    ["CR CR", "\r\r"],
    ["LF then CRLF", "\n\r\n"],   // FAILS before the fix
    ["CRLF then LF", "\r\n\n"],
    ["CRLF then CR", "\r\n\r"],   // FAILS before the fix
  ];
  for (const [name, boundary] of boundaries) {
    it(`delivers a frame ended by ${name}`, async () => {
      const e = loadFixture<StreamEnvelope>("events/heartbeat.json");
      expect(await delivered([`id: ${e.event_id}\ndata: ${JSON.stringify(e)}${boundary}`])).toBe(true);
    });
  }
});

describe("SSE line separators inside a frame", () => {
  it("parses id/data separated by CRLF", async () => {
    const e = loadFixture<StreamEnvelope>("events/heartbeat.json");
    expect(await delivered([`id: ${e.event_id}\r\ndata: ${JSON.stringify(e)}\r\n\r\n`])).toBe(true);
  });
  it("parses id/data separated by a bare CR", async () => {   // FAILS before the fix
    const e = loadFixture<StreamEnvelope>("events/heartbeat.json");
    expect(await delivered([`id: ${e.event_id}\rdata: ${JSON.stringify(e)}\r\r`])).toBe(true);
  });
});

describe("SSE chunk edges", () => {
  it("delivers when \\r\\n\\r\\n straddles two chunks", async () => {
    const e = loadFixture<StreamEnvelope>("events/heartbeat.json");
    const whole = `id: ${e.event_id}\ndata: ${JSON.stringify(e)}\r\n\r\n`;
    const cut = whole.length - 2;
    expect(await delivered([whole.slice(0, cut), whole.slice(cut)])).toBe(true);
  });
  it("delivers when the JSON payload is split mid-token", async () => {
    const e = loadFixture<StreamEnvelope>("events/heartbeat.json");
    const whole = `id: ${e.event_id}\ndata: ${JSON.stringify(e)}\n\n`;
    const cut = Math.floor(whole.length / 2);
    expect(await delivered([whole.slice(0, cut), whole.slice(cut)])).toBe(true);
  });
});

describe("SSE retry policy", () => {
  for (const status of [400, 401, 403, 404, 410, 422]) {
    it(`stops after exactly one attempt on ${status}`, async () => {
      const f = vi.fn(async () => new Response("no", { status }));
      const handle = subscribeToEvents(
        baseConfig(f as unknown as typeof fetch),
        { onEvent: () => {}, onSnapshotRequired: () => {}, onHeartbeat: () => {}, onError: () => {} },
        { reconnectDelayMs: 200 },
      );
      await handle.done;
      expect(f).toHaveBeenCalledTimes(1);
    });
  }
  for (const status of [408, 429, 500, 503]) {
    it(`keeps retrying ${status}`, async () => {
      const f = vi.fn(async () => new Response("later", { status }));
      const handle = subscribeToEvents(
        baseConfig(f as unknown as typeof fetch),
        { onEvent: () => {}, onSnapshotRequired: () => {}, onHeartbeat: () => {}, onError: () => {} },
        { reconnectDelayMs: 200 },
      );
      await new Promise((r) => setTimeout(r, 600));
      handle.close();
      await handle.done;
      expect(f.mock.calls.length).toBeGreaterThan(1);
    });
  }
});
