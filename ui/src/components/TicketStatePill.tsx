import type { Ticket } from "../types";
import { ticketStatusLabel } from "../copy";

export function TicketStatePill({ ticket }: { ticket: Pick<Ticket, "state" | "dependency_blocked"> }) {
  return <span className={`pill state-${ticket.state}`}>{ticketStatusLabel(ticket)}</span>;
}
