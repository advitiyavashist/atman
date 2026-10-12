/**
 * The ticket thread panel (spec §5.9): one ticket's thread with its own
 * composer, then the ticket body, waits-on, review (full head, verdicts),
 * acceptance proof and Details. A side panel over Talk at 1440 px with a
 * resize handle; a full-screen sheet with ‹ Back at 390 px. Esc closes it.
 *
 * Whatever is open is this ticket or an error: the previous ticket's evidence
 * never stays under a new id.
 */

import { useEffect, useRef, useState } from "react";
import type { PlanNode, Post, Ticket } from "../api/types";
import { exactTime, localTime } from "../lib/format";
import { acceptanceProofText, reviewHead, runTiming, runTokens, usageLine } from "../lib/map";
import { sentenceOf, short } from "../v3/model";
import { type Screen } from "../v3/screen";
import { PANEL_WIDTH_KEY, storeGet, storeSet } from "../v3/store";
import { RichText } from "./Badge";
import { ActionRow, Result } from "./Buttons";
import { verbsFor } from "./Cards";
import { Composer, type ComposerProps } from "./Composer";
import { MessageRow } from "./Message";
import { CopyButton, Failure, Missing } from "./bits";

export interface TicketPanelProps {
  id: string;
  node: PlanNode | null;
  ticket: Ticket | null;
  ticketError: unknown;
  loading: boolean;
  /** Messages about this ticket, oldest first (thread?re=). */
  about: Post[] | null;
  aboutError: unknown;
  s: Screen;
  composer: Omit<ComposerProps, "id" | "recipient" | "about" | "fixedAbout" | "agents" | "nodes" | "project" | "readOnly">;
  onClose: () => void;
}

const MIN = 360;
const MAX = 720;

export function TicketPanel(p: TicketPanelProps) {
  const { s, node, ticket } = p;
  const [width, setWidth] = useState<number>(() => Math.min(MAX, Math.max(MIN, storeGet<number>(PANEL_WIDTH_KEY, 480))));
  const [dragging, setDragging] = useState(false);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") p.onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [p]);

  function startDrag(e: React.PointerEvent<HTMLButtonElement>) {
    e.preventDefault();
    const el = e.currentTarget;
    el.setPointerCapture(e.pointerId);
    setDragging(true);
    const move = (ev: PointerEvent) => setWidth(Math.min(MAX, Math.max(MIN, Math.round(window.innerWidth - ev.clientX))));
    const up = () => {
      setDragging(false);
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      setWidth((w) => {
        storeSet(PANEL_WIDTH_KEY, w);
        return w;
      });
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
  }

  const owner = (ticket?.owner || node?.owner || "").trim();
  const mine = !!owner && owner.toLowerCase() === s.operator.toLowerCase();
  const recipient = owner && !mine ? owner : "everyone";
  const result = s.results.get(p.id);
  const nodes = Array.from(s.nodes.values());
  const head = ticket ? reviewHead(ticket.review) : null;
  const proof = ticket ? acceptanceProofText(ticket) : null;
  const title = ticket?.title || node?.title || p.id;

  return (
    <>
      <button type="button" className="scrim" aria-label="Close the ticket panel" onClick={p.onClose} />
      <div className={`panel${dragging ? " dragging" : ""}`} role="dialog" aria-label={p.id} style={{ width }} ref={panel} data-testid="ticket-panel">
        <button type="button" className="handle" aria-label="Resize the panel" title="Drag to resize" onPointerDown={startDrag} />
        <div className="scroll">
          <button type="button" className="back" onClick={p.onClose}>
            ‹ Back
          </button>
          <div className="id">
            {p.id}
            {node?.role || (ticket as { role?: string } | null)?.role ? ` · ${node?.role || (ticket as { role?: string }).role}` : ""}
          </div>
          <h3>{title}</h3>
          {node ? (
            <p className="sentence" data-testid="panel-sentence">
              {sentenceOf(node, { operator: s.operator, nodes, now: s.now })}
            </p>
          ) : ticket ? (
            <p className="sentence">{ticket.status_label}</p>
          ) : null}
          {node ? result ? <Result line={result} /> : <ActionRow verbs={verbsFor(node, s)} readOnly={s.readOnly} /> : null}

          {p.ticketError && !ticket ? <Failure what="The ticket" error={p.ticketError} /> : null}
          {!ticket && !p.ticketError && p.loading ? <p className="muted">Reading {p.id}…</p> : null}

          <h4>Thread about {p.id}</h4>
          <div className="pthread" data-testid="panel-thread">
            {p.aboutError && !p.about ? <Failure what="The thread" error={p.aboutError} /> : null}
            {p.about && p.about.length ? (
              p.about.map((m, i) => <MessageRow key={m.id || i} post={m} operator={s.operator} nodes={s.nodes} details={s.details} onOpen={s.open} />)
            ) : p.about ? (
              <p className="muted" style={{ margin: 0 }}>
                No messages about {p.id} yet.
              </p>
            ) : null}
          </div>
          <Composer id="composer-panel" project={s.project} readOnly={s.readOnly} recipient={recipient} about={p.id} fixedAbout agents={s.agents} nodes={nodes} {...p.composer} />

          {ticket ? (
            <>
              <h4>Ticket</h4>
              <p className="body">
                {ticket.body ? <RichText text={ticket.body} nodes={s.nodes} onOpen={s.open} /> : <Missing what="no ticket text" />}
              </p>
              {ticket.deps.length ? (
                <>
                  <h4>Waits on</h4>
                  <ul className="deps">
                    {ticket.deps.map((d) => (
                      <li key={d.id}>
                        <RichText text={d.id} nodes={s.nodes} onOpen={s.open} /> · {d.state}
                        {d.title ? ` · ${d.title}` : ""}
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
              <h4>Review</h4>
              {head && !head.missing ? (
                <dl className="kv">
                  <dt>Submitted by</dt>
                  <dd>{ticket.owner_at_project || owner || <Missing />}</dd>
                  <dt>Head</dt>
                  <dd>
                    <code data-testid="review-head">{head.head}</code>
                    {head.note ? <span className="missing"> · {head.note}</span> : null}
                  </dd>
                  {ticket.artifact.branch ? (
                    <>
                      <dt>Branch</dt>
                      <dd>
                        <code>{ticket.artifact.branch}</code>
                      </dd>
                    </>
                  ) : null}
                  {ticket.artifact.pr ? (
                    <>
                      <dt>PR</dt>
                      <dd>{ticket.artifact.pr}</dd>
                    </>
                  ) : null}
                  <dt>Status</dt>
                  <dd>{ticket.review.label || ticket.status_label}</dd>
                </dl>
              ) : (
                <p className="muted">No review head submitted yet.</p>
              )}
              {ticket.review.verdicts.length ? (
                ticket.review.verdicts.map((v, i) => (
                  <div key={i} className={`verdict ${v.kind}`} data-testid="verdict">
                    {v.kind} by {v.by || "?"} on <code>{short(v.sha) || "?"}</code>
                    {v.at ? <span className="muted" title={exactTime(v.at)}> · {localTime(v.at)}</span> : null}
                    {v.superseded ? <span className="stale"> · superseded</span> : null}
                    {!v.superseded && !v.applies ? <span className="stale"> · not bound to the current review head</span> : null}
                    {v.notes ? <div className="muted">{v.notes}</div> : null}
                  </div>
                ))
              ) : head && !head.missing ? (
                <div className="verdict">No verdict yet.</div>
              ) : null}
              <h4>Acceptance proof</h4>
              <p className="proof" data-testid="acceptance-proof">
                {proof?.text ? proof.text : ticket.status === "review" ? <span className="muted">None yet. Your accept, with its notes, becomes the proof.</span> : <Missing what={proof?.missing || "no proof recorded"} />}
              </p>
              {ticket.handoff.length ? (
                <>
                  <h4>Handoff</h4>
                  {ticket.handoff.map((h, i) => (
                    <p key={i}>
                      <span className="muted">
                        {h.from} · {h.by || "?"}
                      </span>
                      <br />
                      {h.text}
                    </p>
                  ))}
                </>
              ) : null}
              {s.details ? (
                <div className="details" data-testid="panel-details">
                  <h4>Details</h4>
                  <p className="muted" style={{ fontSize: 12.5 }}>
                    status {ticket.status_label} · owner {owner || "nobody"}
                    {node ? ` · phase ${node.phase}` : ""}
                    {node?.evidence ? ` · ${node.evidence}` : ""}
                  </p>
                  {ticket.runs.length ? (
                    <ul className="runs" data-testid="runs">
                      {ticket.runs.map((r, i) => {
                        const tok = runTokens(r);
                        return (
                          <li key={i}>
                            {r.author || r.seat} · harness {r.harness || "unknown"} · {r.role || "role not recorded"} · {r.state || "?"} · {runTiming(r)} · tokens {tok.label}
                            {r.verdict ? ` · ${r.verdict}` : ""}
                          </li>
                        );
                      })}
                    </ul>
                  ) : (
                    <p className="muted">No runs recorded.</p>
                  )}
                  {ticket.usage.map((u, i) => (
                    <p key={i} className="muted" data-testid="usage">
                      {usageLine(u).text}
                    </p>
                  ))}
                  {ticket.artifact.commit ? (
                    <p className="muted">
                      commit <code>{ticket.artifact.commit}</code>
                    </p>
                  ) : null}
                  {ticket.diff_cmd ? (
                    <div className="cmd">
                      <code>{ticket.diff_cmd}</code>
                      <CopyButton text={ticket.diff_cmd} />
                      <p className="cmd-note">{ticket.diff_note}</p>
                    </div>
                  ) : null}
                  {ticket.steers.length ? (
                    <p className="muted">
                      {ticket.steers.length} steer{ticket.steers.length === 1 ? "" : "s"} recorded
                      {ticket.steers.map((st, i) => (
                        <span key={i}>
                          {" "}
                          · {String(st.kind || "steer")} {String(st.receipt || "")}
                        </span>
                      ))}
                    </p>
                  ) : null}
                  <p className="muted">
                    <code>atm show {p.id}</code>
                  </p>
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      </div>
    </>
  );
}
