import { useState } from "react";
import { Modal } from "../components/Modal";
import { TicketStatePill } from "../components/TicketStatePill";
import { ScenarioPicker } from "../components/ScenarioPicker";
import { ticketDetailScenarios } from "../fixtures";
import { assignmentStateLabel, dependencyWaitingLabel, formatDateTime } from "../copy";
import { toast } from "../components/Toast";
import type { TicketAction } from "../types";

const ACTION_LABEL: Record<TicketAction, string> = {
  assign: "Assign",
  claim: "Claim",
  update: "Post update",
  request_review: "Request review",
  accept: "Accept",
  reject: "Reject",
  block: "Block",
  unblock: "Unblock",
};

export function TicketDetail({
  open,
  initialScenarioKey,
  onClose,
}: {
  open: boolean;
  initialScenarioKey: string;
  onClose: () => void;
}) {
  const [scenarioKey, setScenarioKey] = useState(initialScenarioKey);
  const scenario = ticketDetailScenarios.find((s) => s.key === scenarioKey) ?? ticketDetailScenarios[0];
  const { ticket, updates, reviews, assignment, dependencies, available_actions } = scenario.data;

  return (
    <Modal open={open} onClose={onClose} labelledBy="ticket-detail-title">
      <div className="dialog-head" style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <h2 id="ticket-detail-title" style={{ margin: 0 }}>
          {ticket.id} · {ticket.title}
        </h2>
        <span className="spacer" />
        <button type="button" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "10px 0" }}>
        <TicketStatePill ticket={ticket} />
        {ticket.owner && <span className="tag">owner {ticket.owner}</span>}
        <span className="spacer" />
        <ScenarioPicker scenarios={ticketDetailScenarios} value={scenario.key} onChange={setScenarioKey} label="Try another state" />
      </div>

      {ticket.outcome && <p>{ticket.outcome}</p>}

      {ticket.blocked_reason && (
        <p className="notice">Blocked: {ticket.blocked_reason}</p>
      )}

      {dependencies.length > 0 && (
        <p className="notice">
          {dependencies.map((d) => dependencyWaitingLabel(d.id)).join(", ")}
        </p>
      )}

      {assignment && (
        <p>
          <span className="pill">{assignmentStateLabel[assignment.state]}</span> — {assignment.reason}
        </p>
      )}

      {ticket.acceptance && ticket.acceptance.length > 0 && (
        <>
          <h3>Acceptance</h3>
          <ul className="checklist">
            {ticket.acceptance.map((item, i) => (
              <li key={i}>
                <span className={item.checked ? "check-yes" : "check-no"}>{item.checked ? "✓" : "○"}</span>
                {item.text}
              </li>
            ))}
          </ul>
        </>
      )}

      {ticket.evidence && (
        <details className="evidence" open>
          <summary>Submitted evidence</summary>
          <code className="block">
            branch: {ticket.evidence.branch}
            <br />
            sha: {ticket.evidence.sha}
            <br />
            checks: {ticket.evidence.checks.map((c) => `${c.name}=${c.status}`).join(", ")}
          </code>
        </details>
      )}

      {reviews.length > 0 && (
        <>
          <h3>Reviews</h3>
          {reviews.map((r) => (
            <div className="row" key={r.id}>
              <div>
                <strong>
                  {r.state} by {r.submitted_by.display_name}
                </strong>
                <small>
                  {formatDateTime(r.submitted_at)}
                  {r.decision_notes ? ` · ${r.decision_notes}` : ""}
                </small>
              </div>
            </div>
          ))}
        </>
      )}

      {updates.length > 0 && (
        <>
          <h3>Updates</h3>
          {updates.map((u) => (
            <div className="row" key={u.id}>
              <div>
                <strong>
                  {u.author.display_name}
                  {u.superseded && <span className="tag"> · superseded</span>}
                </strong>
                <small>{u.body}</small>
              </div>
            </div>
          ))}
        </>
      )}

      <div className="dialog-actions">
        {available_actions.length === 0 && <span className="tag">No actions available in this state.</span>}
        {available_actions.map((a) => (
          <button
            key={a}
            className={a === "accept" || a === "claim" ? "primary" : undefined}
            onClick={() => toast(`Preview only — ${ACTION_LABEL[a]} would call the live API in T-184.`)}
          >
            {ACTION_LABEL[a]}
          </button>
        ))}
      </div>
    </Modal>
  );
}
