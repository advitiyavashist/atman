import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { BoardClient, BoardError, subscribeToEvents, type EventStreamHandle, type StreamEnvelope } from "../api";
import { INITIAL_CONNECTION, backoffDelayMs, type ConnectionState } from "./connection";
import type { BoardSession } from "../session";

/**
 * One event stream and one client for the whole dashboard.
 *
 * Screens do not subscribe. They read `revision` — a counter this provider
 * bumps whenever an event arrives that could change what they show — and
 * re-read their own snapshot. That keeps the "what does this event mean for
 * this screen" question out of every screen: an event says something changed,
 * the screen asks the server what it is now. It costs one request per change
 * and it cannot drift from the server, which is the trade this ticket wants.
 */

export interface BoardContextValue {
  client: BoardClient;
  session: BoardSession;
  connection: ConnectionState;
  /** Bumped when a stream event may have invalidated a screen's snapshot. */
  revision: number;
  /**
   * Bumped when the server said our cursor aged out. Screens must re-read from
   * scratch; anything derived from events since the gap is unsound.
   */
  snapshotEpoch: number;
  /** True from the first `snapshot_required` until every screen has re-read. */
  recoveredGap: { at: string; reason: string } | null;
  refreshAll(): void;
}

const BoardContext = createContext<BoardContextValue | null>(null);

export function useBoard(): BoardContextValue {
  const ctx = useContext(BoardContext);
  if (!ctx) throw new Error("useBoard must be used inside <BoardProvider>");
  return ctx;
}

/** Event types that change something a screen renders. Heartbeats do not. */
const INVALIDATING = new Set([
  "ticket_changed",
  "review_changed",
  "agent_changed",
  "assignment_changed",
  "master_lease_changed",
  "activity_appended",
  "message_created",
]);

export function BoardProvider({
  session,
  children,
  /** Test seam: a fixed delay keeps a reconnect assertion from depending on jitter. */
  reconnectDelayMs,
}: {
  session: BoardSession;
  children: ReactNode;
  reconnectDelayMs?: number;
}) {
  const client = useMemo(() => new BoardClient(session.config), [session.config]);
  const [connection, setConnection] = useState<ConnectionState>(INITIAL_CONNECTION);
  const [revision, setRevision] = useState(0);
  const [snapshotEpoch, setSnapshotEpoch] = useState(0);
  const [recoveredGap, setRecoveredGap] = useState<BoardContextValue["recoveredGap"]>(null);

  // Held in a ref, not state: the retry loop reads it on every iteration and a
  // state read there would close over the value from the render that started it.
  const attemptsRef = useRef(0);
  const handleRef = useRef<EventStreamHandle | null>(null);

  const refreshAll = useCallback(() => setRevision((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    const onFrame = (envelope: StreamEnvelope) => {
      if (cancelled) return;
      attemptsRef.current = 0;
      setConnection((prev) => ({
        ...prev,
        phase: "live",
        lastEventAt: new Date().toISOString(),
        attempts: 0,
        nextRetryAt: null,
        lastError: null,
      }));
      if (INVALIDATING.has(envelope.type)) setRevision((n) => n + 1);
    };

    const handle = subscribeToEvents(
      session.config,
      {
        onEvent: onFrame,
        // A heartbeat carries no state change, but it is proof the stream is
        // alive — which is the difference between "idle board" and "dead
        // socket" and the reason the client tracks it separately.
        onHeartbeat: onFrame,
        onSnapshotRequired: (envelope) => {
          if (cancelled) return;
          onFrame(envelope);
          // Our cursor aged out of the retention window. Every screen has to
          // re-read; incremental state from before the gap is not recoverable
          // by listening harder.
          setRecoveredGap({
            at: new Date().toISOString(),
            reason: envelope.resume_hint?.reason ?? "cursor_expired",
          });
          setSnapshotEpoch((n) => n + 1);
          setRevision((n) => n + 1);
        },
        onError: (err) => {
          if (cancelled) return;
          attemptsRef.current += 1;
          const attempts = attemptsRef.current;
          const terminal =
            err instanceof BoardError && err.status >= 400 && err.status < 500 && err.status !== 408 && err.status !== 429;
          const delay = reconnectDelayMs ?? backoffDelayMs(attempts);
          setConnection((prev) => ({
            ...prev,
            phase: terminal ? "offline" : "reconnecting",
            attempts,
            nextRetryAt: terminal ? null : Date.now() + delay,
            lastError: err instanceof Error ? err.message : String(err),
          }));
        },
      },
      {
        // Never 0. See backoffDelayMs — a zero delay against a stream that ends
        // immediately is an unbounded hot loop, not a fast reconnect.
        reconnectDelayMs: reconnectDelayMs ?? 1000,
        signal: controller.signal,
      },
    );
    handleRef.current = handle;

    return () => {
      cancelled = true;
      handle.close();
      controller.abort();
    };
  }, [session.config, reconnectDelayMs]);

  const value = useMemo<BoardContextValue>(
    () => ({ client, session, connection, revision, snapshotEpoch, recoveredGap, refreshAll }),
    [client, session, connection, revision, snapshotEpoch, recoveredGap, refreshAll],
  );

  return <BoardContext.Provider value={value}>{children}</BoardContext.Provider>;
}
