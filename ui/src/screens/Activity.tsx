import { useBoard } from "../state/BoardProvider";
import { useResource } from "../state/useResource";
import { ConnectionBanner } from "../components/ConnectionBanner";
import { ErrorNotice } from "../components/ErrorNotice";
import { EmptyStateView } from "../components/EmptyStateView";
import { formatDateTime } from "../copy";
import type { ActivityResponse } from "../types";

export function Activity() {
  const { connection } = useBoard();
  const feed = useResource<ActivityResponse>((client) => client.listActivity(), []);
  const data = feed.data;

  return (
    <>
      <div className="heading">
        <div>
          <span className="tag">ACTIVITY</span>
          <h1>Activity</h1>
        </div>
        <span className="spacer" />
        <button onClick={feed.refetch} data-testid="activity-refresh">
          Refresh
        </button>
      </div>
      <ConnectionBanner stream={data?.stream ?? null} connection={connection} fetchedAt={feed.fetchedAt} />
      {feed.error && <ErrorNotice error={feed.error} onRetry={feed.refetch} onReload={feed.refetch} />}
      {feed.loading && !data && <p data-testid="activity-loading">Reading the trail…</p>}

      {data && data.empty_state && data.items.length === 0 ? (
        <EmptyStateView empty={data.empty_state} />
      ) : (
        data && (
          <section className="card">
            {data.items.map((event) => (
              <div className="row" key={event.id} data-testid={`activity-${event.id}`}>
                <div>
                  {/* summary is server-authoritative copy — render it, do not
                      re-derive it from the action code. */}
                  <strong>{event.summary}</strong>
                  <small>
                    {event.actor.display_name} · {formatDateTime(event.occurred_at)}
                  </small>
                </div>
                <span className="spacer" />
                <span className="tag">{event.action}</span>
              </div>
            ))}
          </section>
        )
      )}
    </>
  );
}
