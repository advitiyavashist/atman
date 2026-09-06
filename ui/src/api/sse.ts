import { BoardError } from "./errors";
import type { BoardClientConfig } from "./http";
import type { SnapshotRequiredEnvelope, StreamEnvelope } from "./types";
import { assertStreamEnvelope } from "./validate";

/**
 * GET /events returns text/event-stream, not application/json, and needs
 * X-Project-Id and Last-Event-ID headers the browser's native EventSource
 * cannot set — so this reads the fetch Response body as a stream and parses
 * frames by hand instead of using EventSource. `id:` is the frozen
 * zero-padded EventId (docs/storage-notes.md's "lexicographic order matches
 * sequence order"); `data:` is one StreamEnvelope. There is no `event:` line
 * in this contract — the discriminator lives inside the JSON payload's `type`.
 */

export interface StreamHandlers {
  onEvent(event: StreamEnvelope): void;
  /**
   * `Last-Event-ID` aged out of the retention window. The contract's answer
   * is not "keep listening" — re-read the screen snapshot, per
   * docs/contracts/openapi.yaml `/events`.
   */
  onSnapshotRequired(event: SnapshotRequiredEnvelope): void;
  onHeartbeat?(event: StreamEnvelope): void;
  onError?(error: unknown): void;
}

export interface SubscribeOptions {
  /** Resume from here on the very first connect, e.g. after a page reload that saved it. */
  initialLastEventId?: string | null;
  /** 0 for tests; a real caller should back off (T-184's call, not this client's). */
  reconnectDelayMs?: number;
  signal?: AbortSignal;
}

export interface EventStreamHandle {
  /** Stops reconnecting after the current read completes. Pass `signal` for an immediate abort instead. */
  close(): void;
  readonly lastEventId: string | null;
  /** Resolves once the read loop has actually stopped — mainly for tests. */
  readonly done: Promise<void>;
}

function parseFrame(raw: string): { id: string | null; data: string | null } {
  let id: string | null = null;
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("id:")) id = line.slice(3).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  return { id, data: dataLines.length > 0 ? dataLines.join("\n") : null };
}

// The contract only guarantees a blank line between frames, not which line
// ending carries it (CRLF, CR or LF, chosen independently per line — nine
// legal terminator pairs). readFrames normalises every line ending to "\n"
// before matching, so this only ever needs to recognise the normalised form.
const FRAME_BOUNDARY = /\n\n/;

async function readFrames(body: ReadableStream<Uint8Array>, onFrame: (raw: string) => void): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let pending = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        // A chunk that ended mid-CRLF is the last chunk: flush the held CR.
        if (pending) {
          buffer += "\n";
          pending = "";
          let tail: RegExpMatchArray | null;
          while ((tail = buffer.match(FRAME_BOUNDARY))) {
            const at = tail.index as number;
            const raw = buffer.slice(0, at);
            buffer = buffer.slice(at + tail[0].length);
            if (raw.trim().length > 0) onFrame(raw);
          }
        }
        break;
      }
      // Normalise line endings before framing. SSE terminates each line with
      // CRLF, CR *or* LF, independently per line, so nine terminator pairs are
      // legal frame boundaries and a fixed alternation misses some of them.
      // A chunk may also end mid-CRLF, so a trailing lone CR is held back.
      let text = pending + decoder.decode(value, { stream: true });
      pending = "";
      if (text.endsWith("\r")) {
        pending = "\r";
        text = text.slice(0, -1);
      }
      buffer += text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
      let match: RegExpMatchArray | null;
      while ((match = buffer.match(FRAME_BOUNDARY))) {
        const boundary = match.index as number;
        const rawFrame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + match[0].length);
        if (rawFrame.trim().length > 0) onFrame(rawFrame);
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function sleep(ms: number): Promise<void> {
  return ms > 0 ? new Promise((resolve) => setTimeout(resolve, ms)) : Promise.resolve();
}

/**
 * A 4xx (other than 408/429) is not a transient condition — a dead or
 * unauthorized session will return the same status forever, so retrying it
 * is an infinite hot loop against a live server, not resilience. 5xx and the
 * two retry-after-flavored 4xx codes are worth another attempt.
 */
function isTerminalStatus(status: number): boolean {
  return status >= 400 && status < 500 && status !== 408 && status !== 429;
}

export function subscribeToEvents(config: BoardClientConfig, handlers: StreamHandlers, options: SubscribeOptions = {}): EventStreamHandle {
  const fetchImpl = config.fetch ?? globalThis.fetch;
  const reconnectDelayMs = options.reconnectDelayMs ?? 1000;
  let closed = false;
  let lastEventId: string | null = options.initialLastEventId ?? null;

  async function connectOnce(): Promise<void> {
    const headers = new Headers();
    headers.set("Accept", "text/event-stream");
    headers.set("X-Project-Id", config.projectId);
    if (lastEventId) headers.set("Last-Event-ID", lastEventId);
    if (config.agentToken) headers.set("Authorization", `Bearer ${config.agentToken}`);

    const response = await fetchImpl(new URL("events", config.baseUrl.endsWith("/") ? config.baseUrl : `${config.baseUrl}/`), {
      method: "GET",
      headers,
      credentials: config.agentToken ? undefined : config.credentials ?? "include",
      signal: options.signal,
    });

    if (!response.ok || !response.body) {
      handlers.onError?.(new BoardError("unexpected_status", response.status, `GET /events returned ${response.status}`));
      if (isTerminalStatus(response.status)) closed = true;
      return;
    }

    await readFrames(response.body, (raw) => {
      const { id, data } = parseFrame(raw);
      if (!data) return;
      let envelope: StreamEnvelope;
      try {
        envelope = JSON.parse(data);
        assertStreamEnvelope(envelope);
      } catch (err) {
        handlers.onError?.(err);
        return;
      }
      // The frame's `id:` and the payload's `event_id` are the same value by
      // contract; prefer the payload since it is what we just validated.
      lastEventId = envelope.event_id || id;
      if (envelope.type === "snapshot_required") {
        handlers.onSnapshotRequired(envelope as SnapshotRequiredEnvelope);
      } else if (envelope.type === "heartbeat") {
        handlers.onHeartbeat?.(envelope);
      } else {
        handlers.onEvent(envelope);
      }
    });
  }

  const done = (async () => {
    while (!closed && !options.signal?.aborted) {
      try {
        await connectOnce();
      } catch (err) {
        if (options.signal?.aborted) break;
        handlers.onError?.(err);
      }
      if (closed || options.signal?.aborted) break;
      await sleep(reconnectDelayMs);
    }
  })();

  return {
    close() {
      closed = true;
    },
    get lastEventId() {
      return lastEventId;
    },
    done,
  };
}
