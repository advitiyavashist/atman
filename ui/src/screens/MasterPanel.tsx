import { useState } from "react";
import { Modal } from "../components/Modal";
import { ScenarioPicker } from "../components/ScenarioPicker";
import { StreamBanner } from "../components/StreamBanner";
import { masterPanelScenarios } from "../fixtures";
import { assignmentStateLabel, formatDateTime } from "../copy";
import { toast } from "../components/Toast";

export function MasterPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [scenarioKey, setScenarioKey] = useState(masterPanelScenarios[0].key);
  const panel = masterPanelScenarios.find((s) => s.key === scenarioKey)!.data;

  return (
    <Modal open={open} onClose={onClose} labelledBy="master-panel-title">
      <div className="dialog-head" style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <h2 id="master-panel-title" style={{ margin: 0 }}>
          Master panel
        </h2>
        <span className="spacer" />
        <button type="button" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>

      <ScenarioPicker scenarios={masterPanelScenarios} value={scenarioKey} onChange={setScenarioKey} />
      <StreamBanner stream={panel.stream} />

      <div className="kv">
        <dt>Holder</dt>
        <dd>{panel.lease.holder ? panel.lease.holder.display_name : "Unheld — no routing until a master takes over"}</dd>
        <dt>Epoch</dt>
        <dd>{panel.lease.epoch}</dd>
        <dt>Routing mode</dt>
        <dd>{panel.lease.routing_mode.replace(/_/g, " ")}</dd>
        <dt>Paused</dt>
        <dd>{panel.lease.paused ? "Yes" : "No"}</dd>
        <dt>Last sweep</dt>
        <dd>{panel.lease.last_sweep_at ? formatDateTime(panel.lease.last_sweep_at) : "—"}</dd>
        <dt>Next sweep</dt>
        <dd>{panel.lease.next_sweep_at ? formatDateTime(panel.lease.next_sweep_at) : "—"}</dd>
      </div>

      <h3>Queue</h3>
      {panel.queue.length === 0 ? (
        <p>No pending reservations.</p>
      ) : (
        panel.queue.map((a) => (
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
      {panel.decisions.length === 0 ? (
        <p>No routing decisions recorded for this snapshot.</p>
      ) : (
        panel.decisions.map((d, i) => (
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

      <div className="dialog-actions">
        <button
          onClick={() => toast("Preview only — this fixture view does not pause or resume live routing.")}
        >
          {panel.lease.paused ? "Resume assignments" : "Pause assignments"}
        </button>
        <button
          className="primary"
          onClick={() => toast("Preview only — running a check requires the live API (T-184).")}
        >
          Run check
        </button>
      </div>
    </Modal>
  );
}
