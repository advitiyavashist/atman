import type { StreamStatus } from "../types";
import { streamStateCopy } from "../copy";

export function StreamBanner({ stream }: { stream: StreamStatus }) {
  const text = streamStateCopy[stream.state](stream.as_of);
  return (
    <p className={`stream-banner ${stream.state}`} role={stream.state === "stale" ? "status" : undefined}>
      <span className="stream-dot" aria-hidden="true" />
      {text}
    </p>
  );
}
