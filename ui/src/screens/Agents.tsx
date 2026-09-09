import { useState } from "react";
import { useBoard } from "../state/BoardProvider";
import { useResource } from "../state/useResource";
import { useMutation } from "../state/useMutation";
import { ConnectionBanner } from "../components/ConnectionBanner";
import { ErrorNotice } from "../components/ErrorNotice";
import { EmptyStateView } from "../components/EmptyStateView";
import { agentStateLabel, formatDateTime, formatWhen, hookOnlyNote, pendingWriteCopy } from "../copy";
import type { Agent, AgentListResponse, HookHealth } from "../types";

/**
 * The hook doctor.
 *
 * Four booleans in the order the connection actually happens, so a stalled
 * connection reads as a position rather than a set of flags: the config is
 * installed, the server received an event from it, a response was delivered
 * back, and finally the session was adopted. The first `false` is where it
 * stopped, and naming that is more use than four ticks and a shrug.
 *
 * `session_adopted` is deliberately last and is the only one that means the
 * agent is genuinely connected — docs/api-notes.md #4 warns that `Agent.state`
 * is derived and a cached `working` can outlive its session, so the doctor
 * reads health, never state.
 */
const HOOK_STEPS: [keyof Omit<HookHealth, "last_error">, string, string][] = [
  ["config_installed", "Config installed", "The hook entries are in the agent's settings file."],
  ["server_received", "Server received an event", "The board has seen a hook event from this agent."],
  ["response_delivered", "Response delivered", "The board's reply reached the agent's session."],
  ["session_adopted", "Session adopted", "A live session lease is attached. Only this means connected."],
];

function HookDoctor({ agent }: { agent: Agent }) {
  const health = agent.hook_health;
  const firstFailure = HOOK_STEPS.find(([key]) => !health[key]);

  return (
    <details className="evidence" open={Boolean(firstFailure)} data-testid={`hook-doctor-${agent.id}`}>
      <summary>
        Hook health —{" "}
        {firstFailure ? (
          <span data-testid={`hook-stopped-at-${agent.id}`}>stopped at “{firstFailure[1]}”</span>
        ) : (
          "all four steps confirmed"
        )}
      </summary>
      <ul className="checklist">
        {HOOK_STEPS.map(([key, label, why]) => (
          <li key={key} data-testid={`hook-health-${key.replace(/_/g, "-")}`} data-ok={health[key]}>
            <span className={health[key] ? "check-yes" : "check-no"} aria-hidden="true">
              {health[key] ? "✓" : "○"}
            </span>
            <strong>{label}</strong>
            <small>{why}</small>
          </li>
        ))}
        {health.last_error && (
          <li className="notice" data-testid={`hook-last-error-${agent.id}`}>
            Last error: {health.last_error}
          </li>
        )}
      </ul>
    </details>
  );
}

/**
 * Revoke ends a session; it does not take the ticket away.
 *
 * The board keeps the agent's work and its trail — revocation kills the
 * credential and the lease, and the ticket stays where it is (T-180's note on
 * `DELETE /agents/{id}/session-lease`). Saying that next to the button matters
 * because the opposite assumption — that revoking frees the ticket — is the one
 * an operator will make under pressure.
 *
 * The route is also not replayable: a second call is 409 `session_lease_expired`
 * with `reason: already revoked`, which the error copy renders rather than
 * hiding as a no-op.
 */
function RevokeControl({ agent }: { agent: Agent }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const revoke = useMutation((client, requestId) =>
    client.revokeSessionLease(agent.id, {
      request_id: requestId,
      expected_version: agent.version,
      note: note.trim(),
    }),
  );

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} data-testid={`revoke-open-${agent.id}`}>
        Revoke session
      </button>
    );
  }

  return (
    <div className="card" style={{ marginTop: 10 }} data-testid={`revoke-panel-${agent.id}`}>
      <p className="notice">
        Ends this session's credential and lease. The agent's ticket stays claimed and its updates stay in the
        trail — this does not reassign work.
      </p>
      <label htmlFor={`revoke-note-${agent.id}`}>Why (recorded on the agent)</label>
      <input id={`revoke-note-${agent.id}`} value={note} onChange={(e) => setNote(e.target.value)} />
      {revoke.phase === "pending" && <p className="tag" role="status">{pendingWriteCopy}</p>}
      {revoke.error && <ErrorNotice error={revoke.error} />}
      {revoke.phase === "succeeded" && revoke.result && (
        <p className="notice" role="status" data-testid={`revoke-result-${agent.id}`}>
          Lease revoked — the board now reports this agent as {agentStateLabel[revoke.result.state]}.
        </p>
      )}
      <div className="dialog-actions">
        <button onClick={() => { setOpen(false); revoke.reset(); }}>Cancel</button>
        <button
          onClick={() => revoke.run()}
          disabled={revoke.phase === "pending" || note.trim().length === 0}
          data-testid={`revoke-confirm-${agent.id}`}
        >
          Revoke lease
        </button>
      </div>
    </div>
  );
}

export function Agents({ onConnect }: { onConnect: () => void }) {
  const { connection } = useBoard();
  const agents = useResource<AgentListResponse>((client) => client.listAgents(), []);
  const data = agents.data;

  return (
    <>
      <div className="heading">
        <div>
          <span className="tag">AGENTS</span>
          <h1>Agents</h1>
        </div>
        <span className="spacer" />
        <button className="primary" onClick={onConnect} data-testid="connect-agent">
          Connect agent
        </button>
      </div>
      <ConnectionBanner stream={data?.stream ?? null} connection={connection} fetchedAt={agents.fetchedAt} />
      {agents.error && <ErrorNotice error={agents.error} onRetry={agents.refetch} onReload={agents.refetch} />}
      {agents.loading && !data && <p data-testid="agents-loading">Reading the board…</p>}

      {data && data.empty_state && data.items.length === 0 ? (
        <EmptyStateView empty={data.empty_state} icon="◎" onPrimaryAction={onConnect} />
      ) : (
        data?.items.map((agent) => (
          <section className="card" key={agent.id} style={{ marginBottom: 14 }} data-testid={`agent-${agent.id}`}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <strong>{agent.name}</strong>
              <span className="pill" data-testid={`agent-state-${agent.id}`}>
                {agentStateLabel[agent.state]}
              </span>
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
              <dd data-testid={`heartbeat-${agent.id}`}>
                {agent.last_heartbeat_at ? formatWhen(agent.last_heartbeat_at) : "Never"}
              </dd>
              <dt>Last progress</dt>
              <dd>{agent.last_progress_at ? formatWhen(agent.last_progress_at) : "None yet"}</dd>
              <dt>Session lease</dt>
              <dd data-testid={`lease-${agent.id}`}>
                {agent.session
                  ? agent.session.revoked_at
                    ? `Revoked ${formatDateTime(agent.session.revoked_at)}${
                        agent.session.revocation_note ? ` — ${agent.session.revocation_note}` : ""
                      }`
                    : `Expires ${formatDateTime(agent.session.expires_at)}`
                  : "None — this agent has no live session"}
              </dd>
            </div>

            <HookDoctor agent={agent} />

            <div className="dialog-actions" style={{ justifyContent: "flex-start" }}>
              {agent.session && !agent.session.revoked_at && <RevokeControl agent={agent} />}
            </div>
          </section>
        ))
      )}
    </>
  );
}
