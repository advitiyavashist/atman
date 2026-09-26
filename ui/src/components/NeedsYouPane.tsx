/**
 * NEEDS YOU — the read-only queue, from `GET /api/v1/needs-you`.
 *
 * Every item is an **unstructured ask**: a message that named the operator and
 * has had no reply, prose that says DECIDE, a `stuck:` post that has sat for
 * more than an hour, an escalated automated node. The board holds no decision
 * records yet, so prose is never shown as a ruling — the only state here is
 * *asked*, and the label says *asked (unstructured)*.
 *
 * Nothing on this screen writes. Answering happens in the chat, and a real
 * ruling is a later phase with a record behind it.
 */

import type { NeedsYou } from "../api/types";
import { exactTime, localTime } from "../lib/format";
import { Chip, Failure, Missing } from "./bits";

export function NeedsYouPane({
  data,
  error,
  onTicket,
}: {
  data: NeedsYou | null;
  error: unknown;
  onTicket: (id: string) => void;
}) {
  if (error) return <Failure what="Needs you" error={error} />;
  if (!data) return <p className="muted">Reading the queue…</p>;
  return (
    <section className="pane pane-needs" aria-label="Needs you">
      <header className="pane-head">
        <h2>Needs you</h2>
        <span className="muted" data-testid="needs-count">
          {data.count} asked
        </span>
      </header>
      <p className="muted">
        {data.operator ? `Addressed to ${data.operator}. ` : "No operator is configured, so nothing can be addressed to you by name. "}
        {data.note}
      </p>
      <p className="muted">Read-only. Answer in the chat; a ruling needs a record, which this phase does not write.</p>
      {data.items.length === 0 ? (
        <p className="muted">Nothing is waiting on you on this board.</p>
      ) : (
        <ul className="asks" data-testid="asks">
          {data.items.map((it) => (
            <li key={it.id} className="ask">
              <div className="ask-head">
                <span className="post-author">{it.author}</span>
                <Chip tone="unverified" title="prose, not a decision record">
                  {it.label}
                </Chip>
                <span className="muted">{it.why}</span>
                <time className="muted" title={exactTime(it.at)}>
                  {localTime(it.at)}
                </time>
                {it.re ? (
                  <button type="button" className="link-ticket" onClick={() => onTicket(it.re)}>
                    re {it.re}
                  </button>
                ) : null}
              </div>
              <p className="post-text">{it.text || <Missing what="no text recorded" />}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
