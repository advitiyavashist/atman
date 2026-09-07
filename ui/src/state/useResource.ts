import { useCallback, useEffect, useRef, useState } from "react";
import { BoardError } from "../api";
import { useBoard } from "./BoardProvider";

/**
 * One screen's snapshot: what it is, how old it is, and whether we know.
 *
 * The distinction that earns this hook its complexity is `data` + `error` being
 * able to hold values at the same time. When a refetch fails we keep showing
 * the last good snapshot — a dashboard that blanks itself on a hiccup is worse
 * than one that says "this is from 30 seconds ago" — but we never let it keep
 * *claiming* to be current. `fetchedAt` and `stale` are how a screen tells the
 * truth about data it is still displaying.
 */
export interface Resource<T> {
  data: T | null;
  error: BoardError | null;
  loading: boolean;
  /** Server time is authoritative for board state; this is our own read clock. */
  fetchedAt: string | null;
  /** A newer read was attempted and failed, so `data` is known to be behind. */
  stale: boolean;
  refetch(): void;
}

export function useResource<T>(
  load: (client: ReturnType<typeof useBoard>["client"]) => Promise<T>,
  deps: unknown[],
): Resource<T> {
  const { client, revision, snapshotEpoch } = useBoard();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<BoardError | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchedAt, setFetchedAt] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [manual, setManual] = useState(0);

  // Guards against a slow first response landing after a fast second one and
  // overwriting it — the classic out-of-order fetch bug, which on a dashboard
  // shows up as the board silently reverting to an older state.
  const sequence = useRef(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  const refetch = useCallback(() => setManual((n) => n + 1), []);

  useEffect(() => {
    const ticket = ++sequence.current;
    let cancelled = false;
    setLoading(true);
    loadRef
      .current(client)
      .then((result) => {
        if (cancelled || ticket !== sequence.current) return;
        setData(result);
        setError(null);
        setStale(false);
        setFetchedAt(new Date().toISOString());
      })
      .catch((err: unknown) => {
        if (cancelled || ticket !== sequence.current) return;
        setError(
          err instanceof BoardError
            ? err
            : new BoardError("network_error", 0, err instanceof Error ? err.message : String(err)),
        );
        // Keep `data` — but mark it, so nothing downstream can present it as
        // current. A blank screen and a confidently wrong screen are both worse
        // than a dated one that says so.
        setStale(true);
      })
      .finally(() => {
        if (!cancelled && ticket === sequence.current) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, revision, snapshotEpoch, manual, ...deps]);

  return { data, error, loading, fetchedAt, stale, refetch };
}
