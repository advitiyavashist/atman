/**
 * One read, polled.
 *
 * The API is all GETs behind a local process, so the app polls rather than
 * streams (there is no event route, and token streaming would mean reading
 * harness transcripts, which this product does not do). A poll keeps the last
 * good value on screen while a refresh is in flight and reports a failure
 * instead of blanking, because a screen of nothing is not evidence that the
 * board is empty.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export interface Resource<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  /** When this value was read, for a freshness line. */
  readAt: Date | null;
  reload: () => void;
}

export function useResource<T>(
  load: () => Promise<T>,
  deps: ReadonlyArray<unknown>,
  everyMs = 0,
): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [readAt, setReadAt] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);
  const alive = useRef(true);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadRef
      .current()
      .then((value) => {
        if (cancelled || !alive.current) return;
        setData(value);
        setError(null);
        setReadAt(new Date());
      })
      .catch((e) => {
        if (cancelled || !alive.current) return;
        setError(e);
      })
      .finally(() => {
        if (cancelled || !alive.current) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  useEffect(() => {
    if (!everyMs) return;
    const id = window.setInterval(() => setTick((n) => n + 1), everyMs);
    return () => window.clearInterval(id);
  }, [everyMs]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, error, loading, readAt, reload };
}
