import { useState } from "react";
import { useBoard } from "../state/BoardProvider";
import { useResource } from "../state/useResource";
import { useMutation } from "../state/useMutation";
import { ConnectionBanner } from "../components/ConnectionBanner";
import { ErrorNotice } from "../components/ErrorNotice";
import { EmptyStateView } from "../components/EmptyStateView";
import { TicketStatePill } from "../components/TicketStatePill";
import { Modal } from "../components/Modal";
import { dependencyWaitingLabel, pendingWriteCopy } from "../copy";
import { TicketDetail } from "./TicketDetail";
import type { Ticket, TicketListResponse } from "../types";

function NewTicketDialog({ onClose }: { onClose: () => void }) {
  const [title, setTitle] = useState("");
  const [outcome, setOutcome] = useState("");
  const [criterion, setCriterion] = useState("");
  const [role, setRole] = useState("");

  const create = useMutation<[], Ticket>((client, requestId) =>
    client.createTicket({
      request_id: requestId,
      title: title.trim(),
      outcome: outcome.trim(),
      // The contract requires at least one criterion; a ticket without one is
      // 422 missing_acceptance_criteria, so the form requires it too.
      acceptance: [{ text: criterion.trim() }],
      ...(role.trim() ? { role: role.trim() } : {}),
    }),
  );

  const ready = title.trim() && outcome.trim() && criterion.trim();

  return (
    <Modal open onClose={onClose} labelledBy="new-ticket-title">
      <div className="dialog-head" style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <h2 id="new-ticket-title" style={{ margin: 0 }}>
          New ticket
        </h2>
        <span className="spacer" />
        <button type="button" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>

      <label htmlFor="ticket-title">Title</label>
      <input id="ticket-title" data-initial-focus value={title} onChange={(e) => setTitle(e.target.value)} />

      <label htmlFor="ticket-outcome">Outcome</label>
      <textarea id="ticket-outcome" rows={2} value={outcome} onChange={(e) => setOutcome(e.target.value)} />

      <label htmlFor="ticket-criterion">Acceptance criterion</label>
      <input id="ticket-criterion" value={criterion} onChange={(e) => setCriterion(e.target.value)} />

      <label htmlFor="ticket-role">Role (optional)</label>
      <input id="ticket-role" value={role} onChange={(e) => setRole(e.target.value)} />

      {create.phase === "pending" && <p className="tag" role="status">{pendingWriteCopy}</p>}
      {create.error && <ErrorNotice error={create.error} onRetry={() => create.run()} />}
      {/* The id comes from the server — it mints ticket ids, the client cannot
          predict one, and guessing would be exactly the false success this
          ticket is about. */}
      {create.phase === "succeeded" && create.result && (
        <p className="notice" role="status" data-testid="create-result">
          Created {create.result.id}.
        </p>
      )}

      <div className="dialog-actions">
        <button type="button" onClick={onClose}>
          Close
        </button>
        <button
          className="primary"
          onClick={() => create.run()}
          disabled={!ready || create.phase === "pending"}
          data-testid="create-ticket"
        >
          Create ticket
        </button>
      </div>
    </Modal>
  );
}

export function Tickets() {
  const { connection } = useBoard();
  const [search, setSearch] = useState("");
  const [stateFilter, setStateFilter] = useState("");
  const [openTicketId, setOpenTicketId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  // Filtering is a server query, not a client-side slice of one page: the list
  // is paginated, so filtering locally would silently search only page one.
  const list = useResource<TicketListResponse>(
    (client) => client.listTickets(stateFilter ? { state: stateFilter } : {}),
    [stateFilter],
  );
  const data = list.data;

  const visible: Ticket[] = (() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    if (!q) return data.items;
    return data.items.filter((t) => t.id.toLowerCase().includes(q) || t.title.toLowerCase().includes(q));
  })();

  return (
    <>
      <div className="heading">
        <div>
          <span className="tag">TICKETS</span>
          <h1>Tickets</h1>
        </div>
        <span className="spacer" />
        <button className="primary" onClick={() => setCreating(true)} data-testid="new-ticket">
          New ticket
        </button>
      </div>
      <ConnectionBanner stream={data?.stream ?? null} connection={connection} fetchedAt={list.fetchedAt} />
      {list.error && <ErrorNotice error={list.error} onRetry={list.refetch} onReload={list.refetch} />}

      <div className="toolbar">
        <input
          type="search"
          aria-label="Search tickets"
          placeholder="Search loaded tickets"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select aria-label="Filter by state" value={stateFilter} onChange={(e) => setStateFilter(e.target.value)}>
          <option value="">All states</option>
          <option value="open">Ready</option>
          <option value="claimed">In progress</option>
          <option value="review">Awaiting review</option>
          <option value="blocked">Blocked</option>
          <option value="done">Done</option>
        </select>
        {typeof data?.total_matching === "number" && (
          <span className="tag" data-testid="total-matching">
            {data.total_matching} total
          </span>
        )}
        {/* Search runs over what is loaded, and says so rather than implying it
            searched the board. */}
        {search.trim() && (
          <span className="tag">
            showing {visible.length} of {data?.items.length ?? 0} loaded
          </span>
        )}
      </div>

      {list.loading && !data && <p data-testid="tickets-loading">Reading the board…</p>}

      {data && data.empty_state && data.items.length === 0 ? (
        <EmptyStateView empty={data.empty_state} icon="▢" onPrimaryAction={() => setCreating(true)} />
      ) : (
        data && (
          <div className="card table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Ticket</th>
                  <th>Outcome</th>
                  <th>State</th>
                  <th>Agent</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((t) => (
                  <tr key={t.id} data-testid={`ticket-row-${t.id}`}>
                    <td className="id">{t.id}</td>
                    <td>
                      <button className="link" onClick={() => setOpenTicketId(t.id)}>
                        {t.title}
                      </button>
                      {t.dependency_blocked && (
                        <div>
                          <small className="tag">{t.dependencies.map(dependencyWaitingLabel).join(", ")}</small>
                        </div>
                      )}
                    </td>
                    <td>
                      <TicketStatePill ticket={t} />
                    </td>
                    <td>{t.owner ?? "Unassigned"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.next_cursor && (
              <p className="tag" data-testid="more-pages">
                More tickets exist beyond this page.
              </p>
            )}
          </div>
        )
      )}

      {openTicketId && <TicketDetail ticketId={openTicketId} onClose={() => setOpenTicketId(null)} />}
      {creating && <NewTicketDialog onClose={() => setCreating(false)} />}
    </>
  );
}
