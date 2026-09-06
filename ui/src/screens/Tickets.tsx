import { useMemo, useState } from "react";
import { ticketDetailScenarios, ticketListScenarios } from "../fixtures";
import { ScenarioPicker } from "../components/ScenarioPicker";
import { StreamBanner } from "../components/StreamBanner";
import { EmptyStateView } from "../components/EmptyStateView";
import { TicketStatePill } from "../components/TicketStatePill";
import { dependencyWaitingLabel } from "../copy";
import { TicketDetail } from "./TicketDetail";
import type { Ticket } from "../types";

function matchingDetailScenarioKey(ticket: Ticket): string | null {
  const exact = ticketDetailScenarios.find(
    (s) => s.data.ticket.id === ticket.id && s.data.ticket.state === ticket.state,
  );
  if (exact) return exact.key;
  const byId = ticketDetailScenarios.find((s) => s.data.ticket.id === ticket.id);
  return byId?.key ?? null;
}

export function Tickets() {
  const [scenarioKey, setScenarioKey] = useState(ticketListScenarios[0].key);
  const [search, setSearch] = useState("");
  const [openTicketId, setOpenTicketId] = useState<string | null>(null);
  const list = ticketListScenarios.find((s) => s.key === scenarioKey)!.data;

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return list.items;
    return list.items.filter((t) => t.id.toLowerCase().includes(q) || t.title.toLowerCase().includes(q));
  }, [list.items, search]);

  const openDetailKey = openTicketId
    ? matchingDetailScenarioKey(list.items.find((t) => t.id === openTicketId)!)
    : null;

  return (
    <>
      <div className="heading">
        <div>
          <span className="tag">PROJECT / EXAMPLE APP</span>
          <h1>Tickets</h1>
        </div>
        <span className="spacer" />
        <ScenarioPicker scenarios={ticketListScenarios} value={scenarioKey} onChange={setScenarioKey} />
      </div>
      <StreamBanner stream={list.stream} />

      <div className="toolbar">
        <input
          type="search"
          aria-label="Search tickets"
          placeholder="Search tickets"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {typeof list.total_matching === "number" && (
          <span className="tag">{list.total_matching} total</span>
        )}
      </div>

      {list.empty_state && list.items.length === 0 ? (
        <EmptyStateView empty={list.empty_state} />
      ) : (
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
              {filtered.map((t) => (
                <tr key={t.id}>
                  <td className="id">{t.id}</td>
                  <td>
                    {matchingDetailScenarioKey(t) ? (
                      <button className="link" onClick={() => setOpenTicketId(t.id)}>
                        {t.title}
                      </button>
                    ) : (
                      t.title
                    )}
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
        </div>
      )}

      {openTicketId && openDetailKey && (
        <TicketDetail open initialScenarioKey={openDetailKey} onClose={() => setOpenTicketId(null)} />
      )}
    </>
  );
}
