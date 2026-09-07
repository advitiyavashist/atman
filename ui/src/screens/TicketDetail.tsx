import { useState } from "react";
import { Modal } from "../components/Modal";
import { TicketStatePill } from "../components/TicketStatePill";
import { ErrorNotice } from "../components/ErrorNotice";
import { useBoard } from "../state/BoardProvider";
import { useResource } from "../state/useResource";
import { useMutation } from "../state/useMutation";
import { supersededNote } from "../errorCopy";
import { assignmentStateLabel, dependencyWaitingLabel, formatDateTime, pendingWriteCopy } from "../copy";
import type { Review, TicketAction, TicketDetailResponse } from "../types";

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

/** The review the operator would be deciding on: the one still awaiting a decision. */
function pendingReview(reviews: Review[]): Review | null {
  return reviews.find((r) => r.state === "requested") ?? null;
}

/**
 * The decision controls, and the reason they look like this.
 *
 * `ReviewDecisionRequest.evidence_sha` must equal the SHA pinned on the
 * submitted review, and the board answers 422 `invalid_review_evidence` when it
 * does not — that is what makes "done requires acceptance of the exact
 * submitted artifact" enforceable rather than aspirational. So the dashboard
 * never lets the operator type a SHA: it shows the pinned one and sends that
 * exact value. An operator accepting DEMO-2 is accepting `a1b2c3d…`, visibly.
 *
 * Accepting while any check is `failed` is also 422, so the accept control is
 * disabled with the reason shown rather than offered and then refused.
 */
function ReviewDecision({
  ticketId,
  review,
  version,
  onDone,
}: {
  ticketId: string;
  review: Review;
  version: number;
  onDone: () => void;
}) {
  const [notes, setNotes] = useState("");
  // `checks` is optional in the contract and the board omits it entirely when a
  // review carries none, so this must not assume an array.
  const checks = review.evidence.checks ?? [];
  const failing = checks.filter((c) => c.status === "failed");
  const decide = useMutation<[("accept" | "reject")], Review>((client, requestId, decision) =>
    client.decideReview(ticketId, review.id, {
      request_id: requestId,
      expected_version: version,
      decision,
      // Pinned, not typed. This is the artifact the operator is looking at.
      evidence_sha: review.evidence.sha,
      ...(notes.trim() ? { notes: notes.trim() } : {}),
    }),
  );

  return (
    <section className="card" style={{ marginTop: 12 }} data-testid="review-decision">
      <h3>Decide review</h3>
      <div className="kv">
        <dt>Submitted by</dt>
        <dd>{review.submitted_by.display_name}</dd>
        <dt>Branch</dt>
        <dd>{review.evidence.branch}</dd>
        <dt>Pinned SHA</dt>
        <dd>
          <code data-testid="pinned-sha">{review.evidence.sha}</code>
        </dd>
        <dt>Checks</dt>
        <dd data-testid="review-checks">
          {checks.length === 0 ? "none reported" : checks.map((c) => `${c.name}=${c.status}`).join(", ")}
        </dd>
      </div>

      {failing.length > 0 && (
        <p className="notice" data-testid="failing-checks">
          {failing.length === 1 ? "A check has failed" : `${failing.length} checks have failed`} (
          {failing.map((c) => c.name).join(", ")}). The board refuses an accept while any check is failed.
        </p>
      )}

      <label htmlFor="decision-notes">Decision notes</label>
      <textarea id="decision-notes" value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} />

      {decide.phase === "pending" && <p className="tag" role="status">{pendingWriteCopy}</p>}
      {decide.error && <ErrorNotice error={decide.error} onReload={onDone} />}
      {decide.phase === "succeeded" && decide.result && (
        <p className="notice" role="status" data-testid="decision-result">
          Review {decide.result.state} · recorded {formatDateTime(decide.result.decided_at ?? review.submitted_at)}
        </p>
      )}

      <div className="dialog-actions">
        <button
          onClick={() => decide.run("reject")}
          disabled={decide.phase === "pending"}
          data-testid="reject-review"
        >
          Reject
        </button>
        <button
          className="primary"
          onClick={() => decide.run("accept")}
          disabled={decide.phase === "pending" || failing.length > 0}
          data-testid="accept-review"
          title={failing.length > 0 ? "A failed check blocks acceptance." : `Accepts ${review.evidence.sha}`}
        >
          Accept {review.evidence.sha.slice(0, 7)}
        </button>
      </div>
    </section>
  );
}

function BlockControl({
  ticketId,
  version,
  blocked,
}: {
  ticketId: string;
  version: number;
  blocked: boolean;
}) {
  const [reason, setReason] = useState("");
  const mutate = useMutation((client, requestId) =>
    client.setTicketBlocked(ticketId, {
      request_id: requestId,
      expected_version: version,
      blocked: !blocked,
      // Required by the contract when blocking; meaningless when unblocking.
      ...(blocked ? {} : { reason: reason.trim() }),
    }),
  );

  return (
    <section className="card" style={{ marginTop: 12 }} data-testid="block-control">
      <h3>{blocked ? "Unblock" : "Block"}</h3>
      {!blocked && (
        <>
          <label htmlFor="block-reason">Reason</label>
          <input id="block-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
        </>
      )}
      {mutate.phase === "pending" && <p className="tag" role="status">{pendingWriteCopy}</p>}
      {mutate.error && <ErrorNotice error={mutate.error} />}
      <div className="dialog-actions">
        <button
          onClick={() => mutate.run()}
          // A blocked ticket without a recorded reason is not auditable, so the
          // control refuses before the board has to.
          disabled={mutate.phase === "pending" || (!blocked && reason.trim().length === 0)}
          data-testid="toggle-blocked"
        >
          {blocked ? "Unblock ticket" : "Block ticket"}
        </button>
      </div>
    </section>
  );
}

export function TicketDetail({ ticketId, onClose }: { ticketId: string; onClose: () => void }) {
  const { connection } = useBoard();
  const detail = useResource<TicketDetailResponse>((client) => client.getTicket(ticketId), [ticketId]);
  const data = detail.data;

  if (!data) {
    return (
      <Modal open onClose={onClose} labelledBy="ticket-detail-title">
        <h2 id="ticket-detail-title">{ticketId}</h2>
        {detail.loading && <p>Reading {ticketId}…</p>}
        {detail.error && <ErrorNotice error={detail.error} onRetry={detail.refetch} onReload={detail.refetch} />}
      </Modal>
    );
  }

  const { ticket, updates, reviews, assignment, dependencies, available_actions } = data;
  const review = pendingReview(reviews);
  const canDecide = available_actions.includes("accept") || available_actions.includes("reject");

  return (
    <Modal open onClose={onClose} labelledBy="ticket-detail-title">
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
        <span className="tag" data-testid="ticket-version">
          v{ticket.version}
        </span>
        <span className="spacer" />
        <button onClick={detail.refetch} data-testid="detail-refresh">
          Reload
        </button>
      </div>

      {detail.stale && detail.error && <ErrorNotice error={detail.error} onRetry={detail.refetch} />}

      {ticket.outcome && <p>{ticket.outcome}</p>}
      {ticket.blocked_reason && <p className="notice">Blocked: {ticket.blocked_reason}</p>}
      {dependencies.length > 0 && (
        <p className="notice">{dependencies.map((d) => dependencyWaitingLabel(d.id)).join(", ")}</p>
      )}
      {assignment && (
        <p data-testid="assignment-note">
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
            checks:{" "}
            {(ticket.evidence.checks ?? []).length === 0
              ? "none reported"
              : ticket.evidence.checks!.map((c) => `${c.name}=${c.status}`).join(", ")}
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
                  {formatDateTime(r.submitted_at)} · {r.evidence.sha.slice(0, 7)}
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
            <div className="row" key={u.id} data-testid={`update-${u.id}`}>
              <div>
                <strong>
                  {u.author.display_name}
                  {u.superseded && (
                    <span className="tag" data-testid="superseded-tag">
                      {" "}
                      · superseded
                    </span>
                  )}
                </strong>
                <small>{u.body}</small>
                {u.superseded && <small>{supersededNote}</small>}
              </div>
            </div>
          ))}
        </>
      )}

      {canDecide && review && (
        <ReviewDecision ticketId={ticket.id} review={review} version={ticket.version} onDone={detail.refetch} />
      )}

      {(available_actions.includes("block") || available_actions.includes("unblock")) && (
        <BlockControl ticketId={ticket.id} version={ticket.version} blocked={ticket.state === "blocked"} />
      )}

      {available_actions.length === 0 && (
        <p className="tag" data-testid="no-actions">
          No actions available from this state
          {connection.phase !== "live" ? " — and this tab is not receiving live updates." : "."}
        </p>
      )}

      {available_actions.filter((a) => a === "claim" || a === "update" || a === "request_review").length > 0 && (
        <p className="notice" data-testid="agent-only-actions">
          {available_actions
            .filter((a) => a === "claim" || a === "update" || a === "request_review")
            .map((a) => ACTION_LABEL[a])
            .join(", ")}{" "}
          {available_actions.filter((a) => a === "claim" || a === "update" || a === "request_review").length === 1
            ? "is an agent action"
            : "are agent actions"}
          . The board refuses them from an operator session, which has no runtime session to act through — an agent
          performs them from its own worktree.
        </p>
      )}
    </Modal>
  );
}
