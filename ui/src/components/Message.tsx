/**
 * One message in Talk or in a ticket panel: who, the local time, the ticket
 * it is about, the text with ticket badges. Receipts, the harness badge and
 * Copy sit behind Details (§5.6). A receipt is never an acknowledgement; a
 * word that reads like one is refused on screen (`receiptLines`).
 */

import type { PlanNode, Post } from "../api/types";
import { exactTime, localTime } from "../lib/format";
import { harnessChip, receiptLines } from "../lib/map";
import { RichText } from "./Badge";
import { CopyButton } from "./bits";

/**
 * The board snapshot's raw message rows (the Everyone thread) are not post
 * rows: they carry no receipts and no harness badge. They are shown as what
 * they are, with the harness marked not recorded, never filled in.
 */
export function asPost(raw: Record<string, unknown>): Post {
  const s = (k: string) => (typeof raw[k] === "string" ? (raw[k] as string) : "");
  const harness = s("harness");
  return {
    id: s("id"),
    at: s("at"),
    from: s("from"),
    author: s("from"),
    to: s("to"),
    re: s("re"),
    text: s("text"),
    mentions: [],
    kind: s("kind") || "message",
    harness: harness
      ? { value: harness, recorded: true, note: "stamped on the post" }
      : { value: "unknown", recorded: false, note: "no harness recorded for this post" },
    operator: s("via") === "ui-operator",
    broadcast: !s("to"),
    receipts: [],
  };
}

export function MessageRow({
  post,
  operator,
  nodes,
  details,
  onOpen,
}: {
  post: Post;
  operator: string;
  nodes: ReadonlyMap<string, PlanNode>;
  details: boolean;
  onOpen: (id: string) => void;
}) {
  const mine = !!operator && post.from.toLowerCase() === operator.toLowerCase();
  const h = harnessChip(post.harness);
  const receipts = receiptLines(post);
  return (
    <div className={`msg${mine ? " me" : ""}`} data-testid="message">
      <div className="meta">
        <b>{post.from || "?"}</b>
        <span title={exactTime(post.at)}>{localTime(post.at)}</span>
        {post.re ? <span className="re">{post.re}</span> : null}
        {post.broadcast || !post.to ? <span>to everyone</span> : null}
      </div>
      <p className="body">
        <RichText text={post.text} nodes={nodes} onOpen={onOpen} />
      </p>
      {details ? (
        <div className="receipts" data-testid="receipts">
          {receipts.length
            ? receipts.map((r, i) => (
                <span key={i}>
                  {r.words.map((w) => (
                    <span key={w} className="r">
                      {w}
                    </span>
                  ))}
                  {r.refused.length ? (
                    <span className="refused" title="a receipt records delivery, not an agent agreeing to anything">
                      refused as not a delivery fact: {r.refused.join(", ")}
                    </span>
                  ) : null}
                </span>
              ))
            : <span className="r">posted</span>}
          <span className="muted" title={h.title} data-testid="harness-badge">
            harness {h.text}
            {h.recorded ? "" : " · not recorded"}
          </span>
          <CopyButton text={post.text} />
        </div>
      ) : null}
    </div>
  );
}
