import { useState } from "react";
import { agentListScenarios, hookEventsByAgentId } from "../fixtures";
import { ScenarioPicker } from "../components/ScenarioPicker";
import { StreamBanner } from "../components/StreamBanner";
import { EmptyStateView } from "../components/EmptyStateView";
import { agentStateLabel, formatDateTime, hookEventKindLabel, hookOnlyNote } from "../copy";

function HookHealthList({ health }: { health: import("../types").HookHealth }) {
  const items: [string, string, boolean][] = [
    ["config-installed", "Config installed", health.config_installed],
    ["server-received", "Server received", health.server_received],
    ["response-delivered", "Response delivered", health.response_delivered],
    ["session-adopted", "Session adopted", health.session_adopted],
  ];
  return (
    <ul className="checklist">
      {items.map(([key, label, ok]) => (
        <li key={key} data-testid={`hook-health-${key}`} data-ok={ok}>
          <span className={ok ? "check-yes" : "check-no"} aria-hidden="true">
            {ok ? "✓" : "○"}
          </span>
          {label}
        </li>
      ))}
      {health.last_error && <li className="notice">{health.last_error}</li>}
    </ul>
  );
}

export function Agents({ onConnect }: { onConnect: () => void }) {
  const [scenarioKey, setScenarioKey] = useState(agentListScenarios[0].key);
  const list = agentListScenarios.find((s) => s.key === scenarioKey)!.data;

  return (
    <>
      <div className="heading">
        <div>
          <span className="tag">PROJECT / EXAMPLE APP</span>
          <h1>Agents</h1>
        </div>
        <span className="spacer" />
        <ScenarioPicker scenarios={agentListScenarios} value={scenarioKey} onChange={setScenarioKey} />
        <button className="primary" onClick={onConnect}>
          Connect agent
        </button>
      </div>
      <StreamBanner stream={list.stream} />

      {list.empty_state && list.items.length === 0 ? (
        <EmptyStateView empty={list.empty_state} onPrimaryAction={onConnect} />
      ) : (
        list.items.map((agent) => {
          const hookEvent = hookEventsByAgentId[agent.id];
          return (
            <section className="card" key={agent.id} style={{ marginBottom: 14 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <strong>{agent.name}</strong>
                <span className="pill">{agentStateLabel[agent.state]}</span>
                <span className="tag">{agent.role}</span>
                <span className="spacer" />
                <span className="tag">
                  {agent.capacity.active_tickets}/{agent.capacity.max_active_tickets} active
                </span>
              </div>

              {agent.connection_mode === "hook_only" && <p className="notice">{hookOnlyNote}</p>}

              <div className="kv" style={{ marginTop: 10 }}>
                <dt>Current ticket</dt>
                <dd>{agent.current_ticket ?? "None"}</dd>
                <dt>Heartbeat</dt>
                <dd>{agent.last_heartbeat_at ? formatDateTime(agent.last_heartbeat_at) : "Never"}</dd>
                <dt>Last progress</dt>
                <dd>{agent.last_progress_at ? formatDateTime(agent.last_progress_at) : "None yet"}</dd>
                {agent.session?.revoked_at && (
                  <>
                    <dt>Lease revoked</dt>
                    <dd>
                      {formatDateTime(agent.session.revoked_at)} — {agent.session.revocation_note}
                    </dd>
                  </>
                )}
                {hookEvent && (
                  <>
                    <dt>Last hook event</dt>
                    <dd>
                      {hookEventKindLabel[hookEvent.kind]} · {formatDateTime(hookEvent.occurred_at)}
                    </dd>
                  </>
                )}
              </div>

              <details className="evidence">
                <summary>Hook health</summary>
                <HookHealthList health={agent.hook_health} />
              </details>
            </section>
          );
        })
      )}
    </>
  );
}
