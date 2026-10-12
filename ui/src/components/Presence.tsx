/**
 * The presence strip (spec §5.8): every registered seat as a dot, its name and
 * what it is doing now in a few words; the operator last as `boss (you)`.
 * Six visuals, six states, each with its reason on hover and in Details.
 */

import type { AgentRow, PlanNode } from "../api/types";
import { presenceOf, seatStateLine } from "../v3/model";

export function PresenceStrip({
  agents,
  nodes,
  operator,
  details,
  onTalk,
}: {
  agents: ReadonlyArray<AgentRow> | null;
  nodes: ReadonlyArray<PlanNode>;
  operator: string;
  details: boolean;
  onTalk: (seat: string) => void;
}) {
  if (!agents) {
    return (
      <p className="presence" data-testid="presence">
        <span className="missing">seat list unreadable</span>
      </p>
    );
  }
  const seats = agents.filter((a) => a.name && a.name.toLowerCase() !== operator.toLowerCase());
  return (
    <ul className="presence" data-testid="presence" aria-label="Seats">
      {seats.map((a) => {
        const p = presenceOf(a, nodes);
        const why = p.why || seatStateLine(a, nodes);
        return (
          <li key={a.name}>
            <button type="button" className="seat" title={why} onClick={() => onTalk(a.name)} data-testid="seat" data-presence={p.kind}>
              <span className={`dot ${p.kind}`} aria-hidden="true" />
              <b>{a.name}</b>
              <span>·</span>
              <span className="doing">{p.words}</span>
              <span className="sr-only">
                {" "}
                ({p.kind.replace("-", " ")})
              </span>
            </button>
            {details && p.why ? <span className="details inline muted"> · {p.why}</span> : null}
          </li>
        );
      })}
      {operator ? (
        <li>
          <span className="seat me">
            <span className="dot working" aria-hidden="true" />
            <b>{operator}</b>
            <span>(you)</span>
          </span>
        </li>
      ) : null}
      {!seats.length && !operator ? <li className="muted">No seats registered on this board.</li> : null}
    </ul>
  );
}
