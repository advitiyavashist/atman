/**
 * DRILL-DOWN — one ticket, from `GET /api/v1/ticket/{id}`.
 *
 * What an operator needs in order to judge a piece of work, and nothing
 * invented:
 *
 * - **runs** with their token counts, or `unknown` when the board never
 *   recorded them (never 0);
 * - **usage** readings, each with its age;
 * - the **full 40-character review head**, because an accept is bound to
 *   exactly that sha and a shortened one cannot be compared;
 * - **verdicts**, marked when they are superseded or no longer bound to the
 *   current head — a stale accept is not an accept of this work;
 * - **handoff**, **messages about it**, **steers**;
 * - **commit / branch / PR**, and the **copyable diff command** (a read route
 *   may not run git, so the app hands over the command rather than a diff it
 *   cannot produce).
 */

import type { Ticket } from "../api/types";
import { NOT_RECORDED, UNKNOWN, exactTime, localTime } from "../lib/format";
import { acceptState, harnessChip, receiptLines, reviewHead, runTiming, runTokens, usageLine, verdictChip } from "../lib/map";
import { Chip, Command, CopyButton, Failure, Field, Missing } from "./bits";

function Runs({ t }: { t: Ticket }) {
  if (!t.runs.length) return <Missing what="no runs recorded for this ticket" />;
  return (
    <ul className="runs" data-testid="runs">
      {t.runs.map((r, i) => {
        const tok = runTokens(r);
        const h = harnessChip({ value: r.harness, recorded: r.harness !== UNKNOWN, note: "" });
        return (
          <li key={i} className="run">
            <span className="run-seat">{r.author || r.seat}</span>
            <span className="harness" title={h.title}>
              {h.text}
            </span>
            <span className="run-role">{r.role || <Missing what="role not recorded" />}</span>
            <span className="run-state">{runTiming(r)}</span>
            <span className={tok.known ? "run-tokens" : "run-tokens missing"} data-testid="run-tokens">
              {tok.label}
            </span>
            {tok.breakdown ? <span className="muted">{tok.breakdown}</span> : null}
            {(() => {
              const v = verdictChip(r.verdict);
              if (!v) return null;
              return (
                <Chip tone={v.label === "accept" ? "accepted" : "unverified"} title={v.sha ? `recorded at ${v.sha}` : undefined}>
                  {v.label}
                </Chip>
              );
            })()}
            <time className="muted" title={exactTime(r.started)}>
              {localTime(r.started)}
            </time>
          </li>
        );
      })}
    </ul>
  );
}

function Review({ t }: { t: Ticket }) {
  const head = reviewHead(t.review);
  return (
    <div className="review" data-testid="review">
      <Field label="review head">
        {head.head ? (
          <>
            <code className="sha-full" data-testid="review-head">
              {head.head}
            </code>
            <CopyButton text={head.head} />
            {head.note ? <span className="missing"> {head.note}</span> : null}
          </>
        ) : (
          <Missing what="no review head recorded" />
        )}
      </Field>
      <Field label="label">{head.label || <Missing />}</Field>
      {t.review.verdicts.length === 0 ? (
        <Missing what="no structured verdict on this ticket" />
      ) : (
        <ul className="verdicts" data-testid="verdicts">
          {t.review.verdicts.map((v, i) => (
            <li key={i}>
              <Chip tone={v.kind === "accept" && v.applies && !v.superseded ? "accepted" : "unverified"}>{v.kind}</Chip>
              <span>by {v.by || <Missing />}</span>
              <code className="sha">{v.sha || "no sha"}</code>
              <time className="muted" title={exactTime(v.at)}>
                {localTime(v.at)}
              </time>
              {v.superseded ? <span className="missing">superseded</span> : null}
              {v.applies ? null : <span className="missing">not bound to the current review head</span>}
              {v.notes ? <span className="muted">{v.notes}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function TicketPane({
  ticket,
  error,
  loading,
  onClose,
  onTicket,
}: {
  ticket: Ticket | null;
  error: unknown;
  loading: boolean;
  onClose: () => void;
  onTicket: (id: string) => void;
}) {
  if (error) {
    return (
      <section className="pane pane-ticket" aria-label="Ticket detail">
        <header className="pane-head">
          <h2>Ticket</h2>
          <button type="button" className="btn-quiet" onClick={onClose}>
            back to plan
          </button>
        </header>
        <Failure what="The ticket" error={error} />
      </section>
    );
  }
  if (!ticket) {
    return (
      <section className="pane pane-ticket" aria-label="Ticket detail">
        <p className="muted">{loading ? "Reading the ticket…" : "Select a step in the plan."}</p>
      </section>
    );
  }

  const state = acceptState(ticket);
  return (
    <section className="pane pane-ticket" aria-label="Ticket detail" data-testid="ticket-pane">
      <header className="pane-head">
        <h2>
          {ticket.id}{" "}
          <span className="ticket-title" title={ticket.title || undefined}>
            {ticket.title || <Missing what="untitled" />}
          </span>
        </h2>
        <button type="button" className="btn-quiet" onClick={onClose}>
          back to plan
        </button>
      </header>

      <div className="ticket-top">
        <Chip tone={state.tone}>{state.label}</Chip>
        <Field label="owner">{ticket.owner_at_project || ticket.owner || <Missing what="unassigned" />}</Field>
        <Field label="acceptance proof">{ticket.acceptance.proof || <Missing what="no proof recorded" />}</Field>
      </div>

      <h3>Dependencies</h3>
      {ticket.deps.length === 0 ? (
        <p className="muted">none</p>
      ) : (
        <ul className="deps" data-testid="deps">
          {ticket.deps.map((d) => (
            <li key={d.id}>
              <button type="button" className="link-ticket" onClick={() => onTicket(d.id)}>
                {d.id}
              </button>
              <Chip tone={d.state === "accepted" ? "accepted" : "unverified"}>{d.state}</Chip>
              <span className="muted">{d.title}</span>
            </li>
          ))}
        </ul>
      )}

      <h3>Runs</h3>
      <Runs t={ticket} />

      <h3>Usage</h3>
      {ticket.usage.length === 0 ? (
        <Missing what="no usage reading for this ticket's harnesses" />
      ) : (
        <ul className="usage" data-testid="usage">
          {ticket.usage.map((u, i) => {
            const line = usageLine(u);
            return (
              <li key={i}>
                <span className="usage-provider">{line.provider}</span>
                <span className={line.headline.includes(UNKNOWN) ? "missing" : ""}>{line.headline}</span>
                <span className={line.ageUnknown ? "missing" : "muted"} data-testid="usage-age">
                  {line.age}
                </span>
                {line.reset ? <span className="muted">resets {line.reset}</span> : null}
                {line.text ? <span className="muted">{line.text}</span> : null}
              </li>
            );
          })}
        </ul>
      )}

      <h3>Review</h3>
      <Review t={ticket} />

      <h3>Handoff</h3>
      {ticket.handoff.length === 0 ? (
        <Missing what="no handoff note" />
      ) : (
        <ul className="handoff" data-testid="handoff">
          {ticket.handoff.map((h, i) => (
            <li key={i}>
              <span className="muted">
                from {h.from || <Missing />} · by {h.by || <Missing />} ·{" "}
                <time title={exactTime(h.at)}>{localTime(h.at)}</time>
              </span>
              <p>{h.text}</p>
            </li>
          ))}
        </ul>
      )}

      <h3>
        Messages about it <span className="muted">{ticket.messages_total} total</span>
      </h3>
      {ticket.messages.length === 0 ? (
        <Missing what="no messages reference this ticket" />
      ) : (
        <ul className="ticket-msgs" data-testid="ticket-messages">
          {ticket.messages.map((m) => {
            const h = harnessChip(m.harness);
            return (
              <li key={m.id}>
                <span className="post-author">{m.author}</span>
                <span className="harness" title={h.title}>
                  {h.text}
                  {h.recorded ? null : <span className="harness-note"> · not recorded</span>}
                </span>
                <time className="muted" title={exactTime(m.at)}>
                  {localTime(m.at)}
                </time>
                <p className="post-text">{m.text}</p>
                {receiptLines(m).map((r, j) => (
                  <span key={j} className="receipt-inline">
                    <span className="receipt-label">delivery receipt</span> {r.agent}: {r.words.join(" · ") || NOT_RECORDED}
                  </span>
                ))}
              </li>
            );
          })}
        </ul>
      )}

      <h3>Steers</h3>
      {ticket.steers.length === 0 ? (
        <Missing what="no steers on this ticket" />
      ) : (
        <ul className="steers" data-testid="steers">
          {ticket.steers.map((s, i) => {
            const rec = s as { kind?: string; from?: string; text?: string; at?: string; receipt?: string };
            return (
              <li key={i}>
                <Chip tone="neutral">{rec.kind || "steer"}</Chip>
                <span className="muted">
                  from {rec.from || <Missing />} · <time title={exactTime(rec.at)}>{localTime(rec.at)}</time>
                </span>
                <p>{rec.text || <Missing what="no text recorded" />}</p>
                <span className="receipt-inline">
                  <span className="receipt-label">delivery receipt</span> {rec.receipt || NOT_RECORDED}
                </span>
              </li>
            );
          })}
        </ul>
      )}

      <h3>Commit</h3>
      <div className="artifact" data-testid="artifact">
        <Field label="branch">{ticket.artifact.branch || <Missing />}</Field>
        <Field label="commit">{ticket.artifact.commit || <Missing />}</Field>
        <Field label="sha">
          {ticket.artifact.sha ? <code className="sha-full">{ticket.artifact.sha}</code> : <Missing />}
        </Field>
        <Field label="PR">
          {ticket.artifact.pr ? (
            <>
              <code>{ticket.artifact.pr}</code>
              <CopyButton text={ticket.artifact.pr} />
            </>
          ) : (
            <Missing />
          )}
        </Field>
      </div>
      <Command cmd={ticket.diff_cmd} note={ticket.diff_note} />
    </section>
  );
}
