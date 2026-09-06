import { useState } from "react";
import { overviewScenarios } from "../fixtures";
import { ScenarioPicker } from "../components/ScenarioPicker";
import { StreamBanner } from "../components/StreamBanner";
import { EmptyStateView } from "../components/EmptyStateView";
import { formatDateTime } from "../copy";
import { toast } from "../components/Toast";
import type { View } from "../components/NavBar";

export function Overview({
  onNavigate,
  onOpenMasterPanel,
}: {
  onNavigate: (v: View) => void;
  onOpenMasterPanel: () => void;
}) {
  const [scenarioKey, setScenarioKey] = useState(overviewScenarios[0].key);
  const overview = overviewScenarios.find((s) => s.key === scenarioKey)!.data;
  const isEmpty = overview.empty_state && overview.counts.ready + overview.counts.in_progress +
    overview.counts.awaiting_review + overview.counts.blocked === 0 && overview.attention.length === 0;

  return (
    <>
      <div className="heading">
        <div>
          <span className="tag">PROJECT / {overview.project.name.toUpperCase()}</span>
          <h1>Overview</h1>
        </div>
        <span className="spacer" />
        <ScenarioPicker scenarios={overviewScenarios} value={scenarioKey} onChange={setScenarioKey} />
      </div>
      <StreamBanner stream={overview.stream} />

      {isEmpty ? (
        <EmptyStateView empty={overview.empty_state!} onPrimaryAction={() => onNavigate("tickets")} />
      ) : (
        <>
          <div className="metrics">
            <div className="card metric">
              <div className="number">{overview.counts.ready}</div>
              <div className="label">Ready</div>
            </div>
            <div className="card metric">
              <div className="number">{overview.counts.in_progress}</div>
              <div className="label">In progress</div>
            </div>
            <div className="card metric">
              <div className="number">{overview.counts.awaiting_review}</div>
              <div className="label">Awaiting review</div>
            </div>
            <div className="card metric">
              <div className="number">{overview.counts.blocked}</div>
              <div className="label">Blocked</div>
            </div>
            <div className="card metric">
              <div className="number">{overview.counts.dependency_blocked}</div>
              <div className="label">Dependency-blocked</div>
            </div>
          </div>

          <div className="grid-2">
            <section className="card">
              <h2>Needs attention</h2>
              {overview.attention.length === 0 && <p>Nothing needs attention right now.</p>}
              {overview.attention.map((item) => (
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
                        else toast("Fixture view only — no run screen in this build.");
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
                {overview.master.holder ? `Held · ${overview.master.holder.display_name}` : "Unheld"}
              </span>
              <div className="kv" style={{ marginTop: 12 }}>
                <dt>Routing</dt>
                <dd>{overview.master.routing_mode.replace(/_/g, " ")}</dd>
                <dt>Last sweep</dt>
                <dd>{overview.master.last_sweep_at ? formatDateTime(overview.master.last_sweep_at) : "—"}</dd>
                <dt>Next sweep</dt>
                <dd>
                  {overview.master.paused
                    ? "Paused"
                    : overview.master.next_sweep_at
                      ? formatDateTime(overview.master.next_sweep_at)
                      : "—"}
                </dd>
              </div>
              <div className="dialog-actions" style={{ justifyContent: "flex-start", marginTop: 16 }}>
                <button onClick={onOpenMasterPanel}>Open master panel</button>
              </div>
            </section>
          </div>

          {overview.recent_accepted.length > 0 && (
            <section className="card" style={{ marginTop: 18 }}>
              <h2>Recently accepted</h2>
              {overview.recent_accepted.map((review) => (
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

          <p className="tag" style={{ marginTop: 18 }}>
            Counts reflect the selected fixture scenario. They are not live board state.
          </p>
        </>
      )}
    </>
  );
}
