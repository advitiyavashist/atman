import { useBoard } from "../state/BoardProvider";
import { useResource } from "../state/useResource";
import { ConnectionBanner } from "../components/ConnectionBanner";
import { ErrorNotice } from "../components/ErrorNotice";
import { EmptyStateView } from "../components/EmptyStateView";
import { formatDateTime } from "../copy";
import type { OverviewResponse } from "../types";
import type { View } from "../components/NavBar";

export function Overview({
  onNavigate,
  onOpenMasterPanel,
}: {
  onNavigate: (v: View) => void;
  onOpenMasterPanel: () => void;
}) {
  const { connection } = useBoard();
  const overview = useResource<OverviewResponse>((client) => client.getOverview(), []);
  const data = overview.data;

  if (!data) {
    return (
      <>
        <div className="heading">
          <h1>Overview</h1>
        </div>
        <ConnectionBanner stream={null} connection={connection} fetchedAt={overview.fetchedAt} />
        {overview.error && <ErrorNotice error={overview.error} onRetry={overview.refetch} onReload={overview.refetch} />}
        {overview.loading && <p data-testid="overview-loading">Reading the board…</p>}
      </>
    );
  }

  const counts = data.counts;
  const isEmpty =
    Boolean(data.empty_state) &&
    counts.ready + counts.in_progress + counts.awaiting_review + counts.blocked === 0 &&
    data.attention.length === 0;

  return (
    <>
      <div className="heading">
        <div>
          <span className="tag">PROJECT / {data.project.name.toUpperCase()}</span>
          <h1>Overview</h1>
        </div>
        <span className="spacer" />
        <button onClick={overview.refetch} data-testid="overview-refresh">
          Refresh
        </button>
      </div>
      <ConnectionBanner stream={data.stream} connection={connection} fetchedAt={overview.fetchedAt} />
      {/* A failed refetch does not blank the screen, but it must not pass the
          old snapshot off as current either. */}
      {overview.error && <ErrorNotice error={overview.error} onRetry={overview.refetch} onReload={overview.refetch} />}

      {isEmpty ? (
        <EmptyStateView empty={data.empty_state!} onPrimaryAction={() => onNavigate("tickets")} />
      ) : (
        <>
          <div className="metrics">
            {(
              [
                ["ready", "Ready", counts.ready],
                ["in-progress", "In progress", counts.in_progress],
                ["awaiting-review", "Awaiting review", counts.awaiting_review],
                ["blocked", "Blocked", counts.blocked],
                ["dependency-blocked", "Dependency-blocked", counts.dependency_blocked],
              ] as const
            ).map(([key, label, value]) => (
              <div className="card metric" key={key} data-testid={`count-${key}`}>
                <div className="number">{value}</div>
                <div className="label">{label}</div>
              </div>
            ))}
          </div>

          <div className="grid-2">
            <section className="card">
              <h2>Needs attention</h2>
              {data.attention.length === 0 && <p>Nothing needs attention right now.</p>}
              {data.attention.map((item) => (
                <div className="row" key={`${item.kind}-${item.subject_id}`}>
                  <span className="dot-amber" aria-hidden="true">
                    ●
                  </span>
                  <div>
                    <strong>{item.headline}</strong>
                    <small>
                      {item.acknowledged_by
                        ? `Acknowledged by ${item.acknowledged_by.display_name}`
                        : `Raised ${formatDateTime(item.raised_at)}`}
                    </small>
                  </div>
                  <span className="spacer" />
                  {!item.acknowledged_by && (
                    <button
                      onClick={() => {
                        if (item.subject_type === "ticket") onNavigate("tickets");
                        else if (item.subject_type === "agent") onNavigate("agents");
                        else if (item.subject_type === "master_lease") onOpenMasterPanel();
                        else onNavigate("activity");
                      }}
                    >
                      {item.subject_type === "agent" ? "Check agent" : "Review"}
                    </button>
                  )}
                </div>
              ))}
            </section>

            <section className="card">
              <h2>Master</h2>
              <span className="pill">
                {data.master.holder ? `Held · ${data.master.holder.display_name}` : "Unheld"}
              </span>
              <div className="kv" style={{ marginTop: 12 }}>
                <dt>Routing</dt>
                <dd>{data.master.routing_mode.replace(/_/g, " ")}</dd>
                <dt>Last sweep</dt>
                <dd>{data.master.last_sweep_at ? formatDateTime(data.master.last_sweep_at) : "—"}</dd>
                <dt>Next sweep</dt>
                <dd>
                  {data.master.paused
                    ? "Paused"
                    : data.master.next_sweep_at
                      ? formatDateTime(data.master.next_sweep_at)
                      : "—"}
                </dd>
              </div>
              <div className="dialog-actions" style={{ justifyContent: "flex-start", marginTop: 16 }}>
                <button onClick={onOpenMasterPanel}>Open master panel</button>
              </div>
            </section>
          </div>

          {data.recent_accepted.length > 0 && (
            <section className="card" style={{ marginTop: 18 }}>
              <h2>Recently accepted</h2>
              {data.recent_accepted.map((review) => (
                <div className="row" key={review.id}>
                  <div>
                    <strong>
                      {review.ticket_id} accepted by {review.decided_by?.display_name ?? "reviewer"}
                    </strong>
                    <small>
                      {review.submitted_by.display_name} · {review.evidence.sha.slice(0, 7)} ·{" "}
                      {formatDateTime(review.decided_at ?? review.submitted_at)}
                    </small>
                  </div>
                </div>
              ))}
            </section>
          )}
        </>
      )}
    </>
  );
}
