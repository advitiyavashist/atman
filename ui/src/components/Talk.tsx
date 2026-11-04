/**
 * The Talk column (spec §4, §5.6): who the operator talks to, that party's
 * state in one truthful line, the thread, the composer.
 *
 * The recipient is never picked for the operator unless the board chose a
 * lead or exactly one agent is running; otherwise it is Everyone on the board.
 */

import { useEffect, useRef } from "react";
import type { AgentRow, Post, Thread } from "../api/types";
import { usageLine } from "../lib/map";
import { presenceOf, seatStateLine } from "../v3/model";
import type { Screen } from "../v3/screen";
import { ActionButton, type Verb } from "./Buttons";
import { Composer, type ComposerProps } from "./Composer";
import { MessageRow } from "./Message";
import { Failure } from "./bits";

export interface TalkProps {
  s: Screen;
  recipient: string;
  onRecipient: (seat: string) => void;
  lead: string;
  onSetLead: (seat: string) => Promise<void>;
  /** The seat thread, or null while loading / when talking to everyone. */
  thread: Thread | null;
  threadError: unknown;
  /** The board's broadcast window, for Everyone. */
  broadcast: Post[] | null;
  olderPages: Post[];
  onLoadOlder: (before: string) => void;
  composer: Omit<ComposerProps, "id" | "recipient" | "agents" | "nodes" | "project" | "readOnly">;
}

export function Talk(p: TalkProps) {
  const { s } = p;
  const everyone = p.recipient === "everyone";
  const agent = s.agents.find((a) => a.name === p.recipient) || null;
  const nodes = Array.from(s.nodes.values());
  const scroller = useRef<HTMLDivElement>(null);
  const presence = agent ? presenceOf(agent, nodes) : null;
  const anyRunning = s.agents.some((a) => presenceOf(a, nodes).kind === "working");

  const messages: Post[] = everyone ? p.broadcast || [] : [...p.olderPages, ...(p.thread?.messages || [])];
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, p.recipient]);

  const start: Verb = { label: "Start an agent…", onClick: () => s.act({ kind: "start", seat: agent?.name }), kind: "" };
  let stateLine: { text: string; cls: string } | null = null;
  let note: React.ReactNode = null;
  if (everyone) {
    stateLine = anyRunning
      ? { text: "Posts to every seat on this board.", cls: "" }
      : { text: "No agent is running. Messages land on the board; the next agent that wakes reads them.", cls: "" };
    if (!anyRunning) note = (
      <div className="note">
        Nothing is running on this board.
        <br />
        <ActionButton v={start} readOnly={false} />
      </div>
    );
  } else if (agent && presence) {
    const cls = presence.kind === "working" ? "on" : presence.kind === "limited" || presence.kind === "logged-out" ? "warn" : "";
    stateLine = { text: seatStateLine(agent, nodes), cls };
    const held = nodes.find((n) => n.owner === agent.name && (n.phase === "working" || n.phase === "blocked"));
    if (presence.kind === "limited") {
      note = (
        <div className="note">
          {agent.name} is limited until {agent.limit_until || "reset unknown"}. Your message is kept; it reads it when the limit lifts.
          {held ? (
            <>
              <br />
              <ActionButton v={{ label: "Reassign its work…", onClick: () => s.act({ kind: "assign", id: held.id }), kind: "" }} readOnly={s.readOnly} />
            </>
          ) : null}
        </div>
      );
    } else if (presence.kind === "logged-out") {
      note = (
        <div className="note">
          {agent.name} is logged out. Your message is kept; it reads it after <code>{presence.why}</code>.
        </div>
      );
    } else if (presence.kind === "offline") {
      note = (
        <div className="note">
          {agent.name}'s host is offline: {presence.why} Your message is kept; it reads it when the host reconnects.
          <br />
          <ActionButton v={start} readOnly={false} />
        </div>
      );
    } else if (presence.kind !== "working") {
      note = (
        <div className="note">
          {agent.name} is not running. Your message is kept; it reads it when it starts.
          <br />
          <ActionButton v={start} readOnly={false} />
        </div>
      );
    }
  } else if (!everyone) {
    stateLine = { text: `${p.recipient} is not registered on this board.`, cls: "warn" };
  }

  const usage = agent?.provider_usage && typeof agent.provider_usage === "object" ? (agent.provider_usage as Record<string, unknown>) : null;
  const usageText = usage && typeof usage.age === "string" ? usageLine(usage as never).text : "usage not read";
  const detailsLine = agent
    ? [
        usageText,
        `auth ${agent.auth_surface?.label || "not checked"}`,
        `harness ${agent.harness || "not recorded"}`,
        agent.wake_mode ? `wake ${agent.wake_mode}` : "",
        agent.adapter_reason || "",
      ]
        .filter(Boolean)
        .join(" · ")
    : "board thread is broadcast";

  return (
    <aside className="talk" aria-label="Talk" data-testid="talk">
      <div className="th">
        <div className="label">Talk to</div>
        <div className="who">
          <label className="sr-only" htmlFor="talk-with">
            Talk to
          </label>
          <select id="talk-with" value={p.recipient} onChange={(e) => p.onRecipient(e.target.value)} title="Who you are talking to. Never picked for you unless a single agent is running.">
            <option value="everyone">Everyone on the board</option>
            {s.agents
              .filter((a) => a.name.toLowerCase() !== s.operator.toLowerCase())
              .map((a: AgentRow) => (
                <option key={a.name} value={a.name}>
                  {a.name} · {seatStateLine(a, nodes)}
                </option>
              ))}
          </select>
          {agent && p.lead !== agent.name && !s.readOnly ? (
            <button type="button" className="btn quiet" style={{ height: 24, padding: "0 6px", fontSize: 12 }} onClick={() => void p.onSetLead(agent.name)}>
              Set as lead
            </button>
          ) : null}
          {agent && p.lead === agent.name ? <span className="muted" style={{ fontSize: 12 }}>lead</span> : null}
        </div>
        {stateLine ? (
          <div className={`state ${stateLine.cls}`} data-testid="talk-state">
            {stateLine.text}
          </div>
        ) : null}
        {note}
        {s.details ? (
          <div className="details muted" style={{ marginTop: 8, fontSize: 12.5 }} data-testid="talk-details">
            {detailsLine}
          </div>
        ) : null}
      </div>
      <div className="thread" ref={scroller} data-testid="thread">
        {p.threadError && !p.thread && !everyone ? <Failure what="The thread" error={p.threadError} /> : null}
        {!everyone && p.thread?.has_more && p.thread.oldest_id ? (
          <button type="button" className="btn quiet older" onClick={() => p.onLoadOlder(p.olderPages[0]?.id || p.thread!.oldest_id)}>
            Load older messages
          </button>
        ) : null}
        {messages.length ? (
          messages.map((m, i) => <MessageRow key={m.id || i} post={m} operator={s.operator} nodes={s.nodes} details={s.details} onOpen={s.open} />)
        ) : (
          <div className="empty-t">{everyone ? "No messages on the board yet." : `No messages with ${p.recipient} yet.`}</div>
        )}
      </div>
      <Composer id="composer" project={s.project} readOnly={s.readOnly} recipient={p.recipient} agents={s.agents} nodes={nodes} {...p.composer} />
    </aside>
  );
}
