import { useState } from "react";
import { activityScenarios } from "../fixtures";
import { ScenarioPicker } from "../components/ScenarioPicker";
import { StreamBanner } from "../components/StreamBanner";
import { EmptyStateView } from "../components/EmptyStateView";
import { formatDateTime } from "../copy";

export function Activity() {
  const [scenarioKey, setScenarioKey] = useState(activityScenarios[0].key);
  const feed = activityScenarios.find((s) => s.key === scenarioKey)!.data;

  return (
    <>
      <div className="heading">
        <div>
          <span className="tag">PROJECT / EXAMPLE APP</span>
          <h1>Activity</h1>
        </div>
        <span className="spacer" />
        <ScenarioPicker scenarios={activityScenarios} value={scenarioKey} onChange={setScenarioKey} />
      </div>
      <StreamBanner stream={feed.stream} />

      {feed.empty_state && feed.items.length === 0 ? (
        <EmptyStateView empty={feed.empty_state} />
      ) : (
        <section className="card">
          {feed.items.map((event) => (
            <div className="row" key={event.id}>
              <div>
                {/* summary is server-authoritative copy — render it, do not re-derive from action codes */}
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
      )}
    </>
  );
}
