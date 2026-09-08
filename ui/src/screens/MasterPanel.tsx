import { useState } from "react";
import { Modal } from "../components/Modal";
import { ConnectionBanner } from "../components/ConnectionBanner";
import { ErrorNotice } from "../components/ErrorNotice";
import { useBoard } from "../state/BoardProvider";
import { useResource } from "../state/useResource";
import { useMutation } from "../state/useMutation";
import { assignmentStateLabel, formatDateTime, pendingWriteCopy, queuedNotWorkingNote } from "../copy";
import type { AgentListResponse, MasterPanelResponse, TicketListResponse } from "../types";

/**
 * Master controls, all operator-session only.
 *
 * The frozen `securitySchemes` refuses an agent token master authority, and the
 * planner's 12:50Z ruling made that final: lease, pause and assignment are
 * operator-only, with no promotion path (docs/api-notes.md, gap 2). So these
 * controls exist on the dashboard and nowhere else, and a 403
 * `agent_token_insufficient` from here would mean the session file is wrong,
 * not that the operator lacks a permission they could be granted.
 *
 * Every one of them is fenced by a number the caller must have read first —
 * `expected_epoch` for the takeover, `lease_epoch` for pause and assignment. A
 * control that sent the epoch it just rendered without the operator having seen
 * it would defeat the fence, so each one shows the epoch it is acting on.
 */
function TakeLease({ epoch, onDone }: { epoch: number; onDone: () => void }) {
  const take = useMutation((client, requestId) =>
    // Compare-and-swap: two masters racing on the same epoch yield one winner
    // and one 409 master_lease_conflict, which the error copy tells apart from
    // a permissions problem.
    client.takeMasterLease({ request_id: requestId, expected_epoch: epoch }),
  );

  return (
    <>
      {take.phase === "pending" && <p className="tag" role="status">{pendingWriteCopy}</p>}
      {take.error && <ErrorNotice error={take.error} onReload={onDone} />}
      <button onClick={() => take.run()} disabled={take.phase === "pending"} data-testid="take-lease">
        Take master lease (from epoch {epoch})
      </button>
    </>
  );
}

function PauseControl({ epoch, paused, onDone }: { epoch: number; paused: boolean; onDone: () => void }) {
  const toggle = useMutation((client, requestId) =>
    client.setMasterPaused({ request_id: requestId, lease_epoch: epoch, paused: !paused }),
  );

  return (
    <>
      {toggle.phase === "pending" && <p className="tag" role="status">{pendingWriteCopy}</p>}
      {toggle.error && <ErrorNotice error={toggle.error} onReload={onDone} />}
      <button onClick={() => toggle.run()} disabled={toggle.phase === "pending"} data-testid="toggle-pause">
        {paused ? "Resume assignments" : "Pause assignments"}
      </button>
    </>
  );
}

function AssignControl({ epoch, onDone }: { epoch: number; onDone: () => void }) {
  const [ticketId, setTicketId] = useState("");
  const [agentId, setAgentId] = useState("");
  const [reason, setReason] = useState("");
  const tickets = useResource<TicketListResponse>((client) => client.listTickets({ state: "open" }), []);
  const agents = useResource<AgentListResponse>((client) => client.listAgents(), []);

  const chosen = tickets.data?.items.find((t) => t.id === ticketId);
  const assign = useMutation((client, requestId) =>
    client.createAssignment({
      request_id: requestId,
      ticket_id: ticketId,
      agent_id: agentId,
      reason: reason.trim(),
      lease_epoch: epoch,
      // The ticket's own version, not the lease's: the reservation is fenced
      // against the ticket changing under us as well as against a stale master.
      expected_version: chosen?.version ?? 0,
    }),
  );

  return (
    <section className="card" style={{ marginTop: 12 }} data-testid="assign-control">
      <h3>Reserve a ticket for an agent</h3>
      <p className="notice">{queuedNotWorkingNote}</p>

      <label htmlFor="assign-ticket">Ticket</label>
      <select id="assign-ticket" value={ticketId} onChange={(e) => setTicketId(e.target.value)}>
        <option value="">Choose a ready ticket</option>
        {tickets.data?.items.map((t) => (
          <option key={t.id} value={t.id}>
            {t.id} — {t.title} (v{t.version})
          </option>
        ))}
      </select>

      <label htmlFor="assign-agent">Agent</label>
      <select id="assign-agent" value={agentId} onChange={(e) => setAgentId(e.target.value)}>
        <option value="">Choose an agent</option>
        {agents.data?.items.map((a) => (
          <option key={a.id} value={a.id}>
            {a.name} ({a.role}) — {a.capacity.active_tickets}/{a.capacity.max_active_tickets} active
          </option>
        ))}
      </select>

      <label htmlFor="assign-reason">Reason (recorded — routing without one is not auditable)</label>
      <input id="assign-reason" value={reason} onChange={(e) => setReason(e.target.value)} />

      {assign.phase === "pending" && <p className="tag" role="status">{pendingWriteCopy}</p>}
      {assign.error && <ErrorNotice error={assign.error} onReload={onDone} />}
      {assign.phase === "succeeded" && assign.result && (
        <p className="notice" role="status" data-testid="assign-result">
          {assign.result.ticket_id} reserved for {assign.result.agent_id} —{" "}
          {assignmentStateLabel[assign.result.state]}. Expires {formatDateTime(assign.result.expires_at)}.
        </p>
      )}

      <div className="dialog-actions">
        <button
          onClick={() => assign.run()}
          disabled={assign.phase === "pending" || !ticketId || !agentId || reason.trim().length === 0}
          data-testid="create-assignment"
        >
          Reserve
        </button>
      </div>
    </section>
  );
}

export function MasterPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { connection } = useBoard();
  const panel = useResource<MasterPanelResponse>((client) => client.getMasterPanel(), []);
  const data = panel.data;

  if (!open) return null;

  return (
    <Modal open onClose={onClose} labelledBy="master-panel-title">
      <div className="dialog-head" style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <h2 id="master-panel-title" style={{ margin: 0 }}>
          Master panel
        </h2>
        <span className="spacer" />
        <button type="button" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>

      <ConnectionBanner stream={data?.stream ?? null} connection={connection} fetchedAt={panel.fetchedAt} />
      {panel.error && <ErrorNotice error={panel.error} onRetry={panel.refetch} onReload={panel.refetch} />}
      {!data && panel.loading && <p>Reading the master lease…</p>}

      {data && (
        <>
          <div className="kv">
            <dt>Holder</dt>
            <dd data-testid="lease-holder">
              {data.lease.holder
                ? data.lease.holder.display_name
                : "Unheld — no routing until a master takes over"}
            </dd>
            <dt>Epoch</dt>
            <dd data-testid="lease-epoch">{data.lease.epoch}</dd>
            <dt>Expires</dt>
            <dd>{formatDateTime(data.lease.expires_at)}</dd>
            <dt>Routing mode</dt>
            <dd>{data.lease.routing_mode.replace(/_/g, " ")}</dd>
            <dt>Paused</dt>
            <dd data-testid="lease-paused">{data.lease.paused ? "Yes" : "No"}</dd>
            <dt>Last sweep</dt>
            <dd>{data.lease.last_sweep_at ? formatDateTime(data.lease.last_sweep_at) : "—"}</dd>
            <dt>Next sweep</dt>
            <dd>{data.lease.next_sweep_at ? formatDateTime(data.lease.next_sweep_at) : "—"}</dd>
          </div>

          <h3>Queue</h3>
          {data.queue.length === 0 ? (
            <p>No pending reservations.</p>
          ) : (
            data.queue.map((a) => (
              <div className="row" key={a.id}>
                <div>
                  <strong>
                    {a.ticket_id} → {a.agent_id}
                  </strong>
                  <small>
                    {assignmentStateLabel[a.state]} · {a.reason}
                  </small>
                </div>
              </div>
            ))
          )}

          <h3>Recent decisions</h3>
          {data.decisions.length === 0 ? (
            <p>No routing decisions recorded.</p>
          ) : (
            data.decisions.map((d, i) => (
              <div className="row" key={i}>
                <div>
                  <strong>
                    {d.ticket_id}: {d.decision.replace(/_/g, " ")}
                  </strong>
                  <small>{d.reason}</small>
                </div>
              </div>
            ))
          )}

          <AssignControl epoch={data.lease.epoch} onDone={panel.refetch} />

          <div className="dialog-actions">
            <PauseControl epoch={data.lease.epoch} paused={data.lease.paused} onDone={panel.refetch} />
            <TakeLease epoch={data.lease.epoch} onDone={panel.refetch} />
          </div>
        </>
      )}
    </Modal>
  );
}
