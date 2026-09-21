/**
 * One read, polled — and never shown against the wrong question.
 *
 * The API is all GETs behind a local process, so the app polls rather than
 * streams (there is no event route, and token streaming would mean reading
 * harness transcripts, which this product does not do).
 *
 * Two rules, and the second one exists because breaking it did real damage:
 *
 * 1. **A refresh that fails keeps the last good value**, and reports the
 *    failure beside it. A screen of nothing is not evidence that the board is
 *    empty.
 * 2. **A value is only ever offered back for the deps it was read for.** Rule 1
 *    on its own let a payload read for one project stay on screen after the
 *    operator switched to another: the shell then composed one board's records
 *    with the other board's slug, and the plan showed the first project's steps
 *    as `owner@other-project`. Mislabelling one project's work as another's is
 *    the worst thing this app can do, so `data` is held together with the deps
 *    that produced it and is withheld the moment they differ. A failed read
 *    after a switch therefore lands on the caller's error path, which is what
 *    it should have done all along.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export interface Resource<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  /** When this value was read, for a freshness line. Null whenever data is. */
  readAt: Date | null;
  reload: () => void;
}

/** Identity per element: these deps are the client and a project slug. */
function sameDeps(a: ReadonlyArray<unknown>, b: ReadonlyArray<unknown>): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) if (!Object.is(a[i], b[i])) return false;
  return true;
}

interface Entry<T> {
  /** The deps this value, error and stamp belong to. */
  deps: ReadonlyArray<unknown>;
  data: T | null;
  error: unknown;
  readAt: Date | null;
}

/**
 * @param enabled false while the question itself is not known yet — before the
 *   shell has resolved which project it is looking at, for instance. A
 *   disabled read fires nothing and reports itself as still loading, which
 *   stops the app reading the started board first and the chosen one a moment
 *   later, and with it the flicker of one project's data on the way to
 *   another's.
 */
export function useResource<T>(
  load: () => Promise<T>,
  deps: ReadonlyArray<unknown>,
  everyMs = 0,
  enabled = true,
): Resource<T> {
  const [entry, setEntry] = useState<Entry<T> | null>(null);
  const [tick, setTick] = useState(0);
  const alive = useRef(true);
  const loadRef = useRef(load);
  loadRef.current = load;
  // Read in the effect below without making it re-run: a new read for the same
  // deps may keep the previous value, a read for different deps may not.
  const entryRef = useRef<Entry<T> | null>(null);
  entryRef.current = entry;

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const forDeps = deps;
    loadRef
      .current()
      .then((value) => {
        if (cancelled || !alive.current) return;
        setEntry({ deps: forDeps, data: value, error: null, readAt: new Date() });
      })
      .catch((e) => {
        if (cancelled || !alive.current) return;
        const held = entryRef.current;
        // Same question, failed refresh: keep what is on screen and say so.
        // Different question: there is nothing honest to show, so show nothing
        // but the failure.
        const keep = held && sameDeps(held.deps, forDeps);
        setEntry({
          deps: forDeps,
          data: keep ? held.data : null,
          error: e,
          readAt: keep ? held.readAt : null,
        });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, enabled]);

  useEffect(() => {
    if (!everyMs || !enabled) return;
    const id = window.setInterval(() => setTick((n) => n + 1), everyMs);
    return () => window.clearInterval(id);
  }, [everyMs, enabled]);

  const reload = useCallback(() => setTick((n) => n + 1), []);

  // The gate: nothing read for other deps leaves this hook, not even while the
  // read for the current deps is still in flight. "Loading" is therefore the
  // same statement as "no answer for this question yet".
  const current = enabled && entry && sameDeps(entry.deps, deps) ? entry : null;
  return {
    data: current?.data ?? null,
    error: current?.error ?? null,
    readAt: current?.data ? current.readAt : null,
    loading: !current,
    reload,
  };
}
