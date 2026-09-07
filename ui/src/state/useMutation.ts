import { useCallback, useRef, useState } from "react";
import { BoardError } from "../api";
import { useBoard } from "./BoardProvider";

/**
 * Every write the dashboard makes goes through here, and the reason is the
 * defect class this ticket names: an operation that reports success while
 * doing something else, or nothing.
 *
 * Three rules, each of which has bitten this board in a live incident:
 *
 * 1. **No optimistic state.** Nothing on screen changes until the server's
 *    response is in hand. The result is built from the returned record, never
 *    from the request we sent. A UI that renders what it *asked for* cannot
 *    show a 403 that refused it.
 *
 * 2. **One request_id per intent, reused across retries of that intent.** A
 *    fresh id per retry defeats idempotency — the point of the key is that a
 *    retry of the same intent is recognised as the same act. A shared id across
 *    *different* intents is the opposite failure and is what makes every
 *    fixture-driven mutation after the first come back 409 `request_id_reused`
 *    (docs/api-notes.md). So: minted per intent, held until that intent
 *    succeeds or is abandoned.
 *
 * 3. **`superseded` is not success.** A 201 carrying `superseded: true` means
 *    the write was recorded against a session that no longer owns the ticket.
 *    It is a real state with its own copy, not a green tick.
 */

export type MutationPhase = "idle" | "pending" | "succeeded" | "failed";

export interface MutationState<T> {
  phase: MutationPhase;
  /** Only ever the server's returned record. */
  result: T | null;
  error: BoardError | null;
  /** True when the server accepted the write but a newer session owns the work. */
  superseded: boolean;
}

export interface Mutation<Args extends unknown[], T> extends MutationState<T> {
  run(...args: Args): Promise<T | null>;
  reset(): void;
  /** The id this intent will retry under. Shown in error copy so a 409 is traceable. */
  requestId: string;
}

function newRequestId(): string {
  // crypto.randomUUID is present in every browser this dashboard supports and
  // in Node 19+; the fallback keeps jsdom setups without it from throwing.
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return "req-" + Math.random().toString(16).slice(2) + Date.now().toString(16);
}

export function useMutation<Args extends unknown[], T>(
  perform: (client: ReturnType<typeof useBoard>["client"], requestId: string, ...args: Args) => Promise<T>,
  options: { onSuccess?(result: T): void } = {},
): Mutation<Args, T> {
  const { client, refreshAll } = useBoard();
  const [state, setState] = useState<MutationState<T>>({
    phase: "idle",
    result: null,
    error: null,
    superseded: false,
  });
  const requestIdRef = useRef<string>(newRequestId());
  const performRef = useRef(perform);
  performRef.current = perform;
  const onSuccessRef = useRef(options.onSuccess);
  onSuccessRef.current = options.onSuccess;
  // A second click while the first is in flight must not become a second write.
  const inFlight = useRef(false);

  const run = useCallback(
    async (...args: Args): Promise<T | null> => {
      if (inFlight.current) return null;
      inFlight.current = true;
      setState({ phase: "pending", result: null, error: null, superseded: false });
      try {
        const result = await performRef.current(client, requestIdRef.current, ...args);
        const superseded = Boolean((result as { superseded?: boolean } | null)?.superseded);
        setState({ phase: "succeeded", result, error: null, superseded });
        // This intent is spent. The next one is a different act and gets its
        // own key; reusing this one would make it a replay of the last write.
        requestIdRef.current = newRequestId();
        onSuccessRef.current?.(result);
        // Re-read rather than patch: the server just changed something and it
        // knows the whole consequence, which a local patch would guess at.
        refreshAll();
        return result;
      } catch (err: unknown) {
        const error =
          err instanceof BoardError
            ? err
            : new BoardError("network_error", 0, err instanceof Error ? err.message : String(err));
        // The request id is deliberately NOT rotated here: a retry of this same
        // intent must present the same key so the board can recognise it.
        setState({ phase: "failed", result: null, error, superseded: false });
        return null;
      } finally {
        inFlight.current = false;
      }
    },
    [client, refreshAll],
  );

  const reset = useCallback(() => {
    // Abandoning an intent, so the next one is genuinely new.
    requestIdRef.current = newRequestId();
    setState({ phase: "idle", result: null, error: null, superseded: false });
  }, []);

  return { ...state, run, reset, requestId: requestIdRef.current };
}
