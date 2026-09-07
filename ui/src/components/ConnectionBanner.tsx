import { displayedFreshness, type ConnectionState } from "../state/connection";
import { formatTime, noSnapshotCopy, streamStateCopy } from "../copy";
import type { StreamStatus } from "../types";

/**
 * The one place the dashboard states how fresh what you are looking at is.
 *
 * It renders the *combined* judgement (see state/connection.ts): the server's
 * claim about the board, downgraded by what this client knows about its own
 * socket. When the client downgrades the server, it says so — an operator who
 * can see "the server said live, we are not connected" can tell a board outage
 * from a browser one, and that is the first thing they will want to know.
 */
export function ConnectionBanner({
  stream,
  connection,
  fetchedAt,
}: {
  stream: StreamStatus | null;
  connection: ConnectionState;
  fetchedAt?: string | null;
}) {
  const freshness = displayedFreshness(stream, connection);
  const asOf = freshness.asOf ?? fetchedAt;
  const text = asOf ? streamStateCopy[freshness.state](asOf) : noSnapshotCopy;

  return (
    <p
      className={`stream-banner ${freshness.state}`}
      role={freshness.state === "stale" ? "alert" : "status"}
      data-testid="connection-banner"
      data-stream-state={freshness.state}
      data-downgraded={freshness.downgraded}
    >
      <span className="stream-dot" aria-hidden="true" />
      {text}
      {connection.phase === "reconnecting" && connection.nextRetryAt && (
        <span className="tag" style={{ marginLeft: 8 }}>
          retry #{connection.attempts} at {formatTime(new Date(connection.nextRetryAt).toISOString())}
        </span>
      )}
      {connection.phase === "offline" && (
        <span className="tag" style={{ marginLeft: 8 }}>
          not retrying — {connection.lastError ?? "the board refused the stream"}
        </span>
      )}
      {freshness.downgraded && (
        <span className="tag" style={{ marginLeft: 8 }} data-testid="downgrade-note">
          the board's last snapshot said “{stream?.state}”, but this tab is not receiving events
        </span>
      )}
    </p>
  );
}
