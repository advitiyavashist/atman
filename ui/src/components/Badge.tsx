/**
 * A ticket badge wherever chat mentions a ticket (spec §5.9): id, phase word,
 * owner. Only an id that is on this board's plan becomes a badge; any other
 * stays plain text, so the badge itself is a truthful claim that the ticket
 * exists here.
 */

import type { PlanNode } from "../api/types";
import { textParts } from "../lib/map";
import { acceptedLabel, bucketOf, phaseWord } from "../v3/model";

export function TicketBadge({ node, onOpen }: { node: PlanNode; onOpen: (id: string) => void }) {
  const who = node.owner || (node.status === "done" ? acceptedLabel(node.review?.label)?.by : "") || "nobody";
  return (
    <button
      type="button"
      className={`badge ${bucketOf(node)}`}
      onClick={(e) => {
        e.stopPropagation();
        onOpen(node.id);
      }}
      title={node.title}
      data-testid="ticket-badge"
    >
      <i aria-hidden="true" />
      <span className="id">{node.id}</span>
      <span className="st">
        {phaseWord(node)} · {who}
      </span>
    </button>
  );
}

/** Message text with badges for the tickets on this board and bold mentions. */
export function RichText({
  text,
  nodes,
  onOpen,
}: {
  text: string;
  nodes: ReadonlyMap<string, PlanNode>;
  onOpen: (id: string) => void;
}) {
  const known = new Set(nodes.keys());
  // A `#T-001` is the same mention as a bare `T-001`; drop the hash so the badge reads once.
  const parts = textParts(text.replace(/#(T-\d{1,7})\b/g, "$1"), known);
  return (
    <>
      {parts.map((part, i) => {
        if (part.kind === "ticket") {
          const node = nodes.get(part.value);
          return node ? <TicketBadge key={i} node={node} onOpen={onOpen} /> : <span key={i}>{part.value}</span>;
        }
        if (part.kind === "mention") return <b key={i} className="mention">@{part.value}</b>;
        return <span key={i}>{part.value}</span>;
      })}
    </>
  );
}
