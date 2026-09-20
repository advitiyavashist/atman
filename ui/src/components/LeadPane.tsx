/**
 * CHAT with the project's lead.
 *
 * Left of the plan, because the point of the app is talking to the lead while
 * the plan is visible. Four parts:
 *
 * - the **title pill**: `lead · seat@project`, its harness, and whether it
 *   takes mid-run messages or answers on its next turn;
 * - the **status strip**: liveness, how long the current run has been going,
 *   when output was last seen, any limit, auth, and the harness usage reading
 *   *with its age*;
 * - the **thread**: each post with `seat@project`, a harness badge (`unknown`
 *   when the board holds none), the local time, a copy button, and its
 *   delivery receipts, labelled as receipts;
 * - the **composer**, which posts as the operator through `POST /msg`. With no
 *   operator configured it does not pretend: it shows the command to set one.
 *
 * Paging back through history is `before=<oldest_id>`, which the thread route
 * pages through the whole live log, not just the snapshot's newest 40.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { AtmanApi } from "../api/client";
import type { Lead, Post, Thread } from "../api/types";
import { UNKNOWN, ago, exactTime, localTime } from "../lib/format";
import { authorOf, harnessChip, receiptLines, runTokens, textParts, usageLine } from "../lib/map";
import { Chip, Command, CopyButton, Failure, Missing } from "./bits";

function HarnessBadge({ post }: { post: Post }) {
  const h = harnessChip(post.harness);
  return (
    <span
      className={`harness ${h.recorded ? "harness-recorded" : "harness-unrecorded"}`}
      title={h.title}
      data-testid="harness-badge"
    >
      {h.text}
      {h.recorded ? null : <span className="harness-note"> · not recorded</span>}
    </span>
  );
}

function PostText({ text, knownTickets, onTicket }: { text: string; knownTickets: ReadonlySet<string>; onTicket: (id: string) => void }) {
  return (
    <p className="post-text">
      {textParts(text, knownTickets).map((part, i) => {
        if (part.kind === "ticket") {
          return (
            <button key={i} type="button" className="link-ticket" onClick={() => onTicket(part.value)}>
              {part.value}
            </button>
          );
        }
        if (part.kind === "mention") return <b key={i} className="mention">@{part.value}</b>;
        return <span key={i}>{part.value}</span>;
      })}
    </p>
  );
}

function Receipts({ post }: { post: Post }) {
  const lines = receiptLines(post);
  if (!lines.length) return null;
  return (
    <ul className="receipts" data-testid="receipts">
      {lines.map((r, i) => (
        <li key={i}>
          <span className="receipt-label">delivery receipt</span>
          <span className="receipt-agent">{r.agent}</span>
          <span className="receipt-words">{r.words.length ? r.words.join(" · ") : <Missing what="no delivery recorded" />}</span>
          {r.refused.length ? (
            <span className="receipt-refused" title="a receipt records delivery, not an agent agreeing to anything">
              refused as not a delivery fact: {r.refused.join(", ")}
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function PostRow({
  post,
  project,
  knownTickets,
  onTicket,
}: {
  post: Post;
  project: string;
  knownTickets: ReadonlySet<string>;
  onTicket: (id: string) => void;
}) {
  return (
    <li className={`post${post.operator ? " post-operator" : ""}`} data-testid="post">
      <div className="post-head">
        <span className="post-author">{authorOf(post, project)}</span>
        {post.operator ? <Chip tone="neutral">operator</Chip> : null}
        <HarnessBadge post={post} />
        <time className="post-at" title={exactTime(post.at)}>
          {localTime(post.at)}
        </time>
        {post.re ? (
          <button type="button" className="link-ticket" onClick={() => onTicket(post.re)}>
            re {post.re}
          </button>
        ) : null}
        <CopyButton text={post.text} />
      </div>
      <PostText text={post.text} knownTickets={knownTickets} onTicket={onTicket} />
      <Receipts post={post} />
    </li>
  );
}

function StatusStrip({ lead }: { lead: Lead }) {
  const s = lead.status;
  if (!s) return null;
  const usage = usageLine(s.usage);
  const lastOut = ago(s.last_output_at);
  return (
    <div className="strip" data-testid="lead-status">
      <span className={`live live-${(s.state || UNKNOWN).toLowerCase()}`}>{s.state || UNKNOWN}</span>
      {s.detail ? <span className="strip-detail">{s.detail}</span> : null}
      <span className="strip-item">
        run{" "}
        {s.running ? (
          <>
            {s.running.ticket || UNKNOWN}
            {" · "}
            {s.running.elapsed_s === null ? <Missing what="elapsed not recorded" /> : `${Math.round(s.running.elapsed_s / 60)}m`}
            {" · "}
            {runTokens({ tokens: s.running.tokens, tokens_in: null, tokens_out: null, tokens_label: "" }).label}
          </>
        ) : (
          "none"
        )}
      </span>
      <span className="strip-item">
        last output {lastOut ? `${lastOut}${s.last_output_source ? ` (${s.last_output_source})` : ""}` : <Missing what="not recorded" />}
      </span>
      <span className="strip-item">
        limit {s.limit ? `until ${s.limit_until || UNKNOWN}` : "none"}
      </span>
      <span className="strip-item">auth {s.auth.label || s.auth.state || UNKNOWN}</span>
      <span className="strip-item" data-testid="lead-usage">
        {usage.provider} {usage.headline} · <span className={usage.ageUnknown ? "missing" : ""}>{usage.age}</span>
        {usage.reset ? ` · resets ${usage.reset}` : ""}
      </span>
      <span className="strip-item">wake {s.wake_mode || UNKNOWN}</span>
    </div>
  );
}

function CannotAnswer({ lead }: { lead: Lead }) {
  const c = lead.status?.cannot_answer;
  if (!c) return null;
  return (
    <div className="warn" role="status" data-testid="cannot-answer">
      <p>
        <strong>{c.kind.replace("_", " ")}:</strong> {c.text} Your message still lands on the board.
      </p>
      <Command cmd={c.cmd} />
    </div>
  );
}

function Picker({ lead, onPick, canWrite }: { lead: Lead; onPick: (seat: string) => void; canWrite: boolean }) {
  return (
    <div className="picker" data-testid="lead-picker">
      <h3>Pick who you talk to on this project</h3>
      <p className="muted">
        Nothing is chosen for you. {lead.lead_note || "master.json.lead is unset."}
      </p>
      {lead.picker.length === 0 ? <Missing what="no registered seats on this board" /> : null}
      <ul className="picker-list">
        {lead.picker.map((row) => (
          <li key={row.seat}>
            <button type="button" className="btn" disabled={!canWrite} onClick={() => onPick(row.seat)}>
              {row.seat}@{lead.project}
            </button>
            <span className="harness">{row.harness}</span>
            <span className="muted">{row.capability}</span>
            <CopyButton text={`atm lead set ${row.seat}`} label="Copy command" />
          </li>
        ))}
      </ul>
      {canWrite ? null : (
        <p className="muted">
          Picking a lead is a write, and this app has no operator. Use the command beside a seat.
        </p>
      )}
    </div>
  );
}

function Composer({
  api,
  thread,
  lead,
  onPosted,
}: {
  api: AtmanApi;
  thread: Thread;
  lead: Lead;
  onPosted: () => void;
}) {
  const [text, setText] = useState("");
  const [re, setRe] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string>("");
  const operator = thread.operator || lead.operator;
  const note = thread.operator_note || lead.operator_note;
  const midrun = lead.status?.capability.midrun;

  if (!operator) {
    return (
      <div className="composer composer-off" data-testid="composer-no-operator">
        <p>
          <strong>No operator is configured, so this app cannot post.</strong> It stays read-only until one is
          set. {note}
        </p>
        <Command
          cmd="atm ui --operator <name>"
          note="The name needs agents/<name>.json on this board (atm join <name>) and must not be a harness-run seat."
        />
      </div>
    );
  }

  async function send() {
    const body = text.trim();
    if (!body || busy) return;
    setBusy(true);
    setFailed("");
    try {
      await api.postMessage({ from: operator, text: body, to: thread.with, re: re.trim() || undefined });
      setText("");
      setRe("");
      onPosted();
    } catch (e) {
      setFailed(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="composer"
      data-testid="composer"
      onSubmit={(e) => {
        e.preventDefault();
        void send();
      }}
    >
      <label className="sr-only" htmlFor="composer-text">
        Tell the lead
      </label>
      <textarea
        id="composer-text"
        value={text}
        rows={3}
        placeholder={`Tell ${thread.with || "the lead"}…`}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="composer-foot">
        <label htmlFor="composer-re">re</label>
        <input
          id="composer-re"
          value={re}
          placeholder="T-1110"
          onChange={(e) => setRe(e.target.value)}
          size={10}
        />
        <span className="muted">
          as {operator} (operator) → {thread.with}
        </span>
        <button type="submit" className="btn" disabled={busy || !text.trim()}>
          {busy ? "posting…" : "Send"}
        </button>
      </div>
      {midrun === false ? (
        <p className="muted" data-testid="next-turn-note">
          {thread.with} answers on its next turn; it cannot be interrupted mid-run.
        </p>
      ) : null}
      {failed ? (
        <p className="failure" role="alert">
          Not posted. {failed}
        </p>
      ) : null}
    </form>
  );
}

export function LeadPane({
  api,
  project,
  lead,
  leadError,
  thread,
  threadError,
  knownTickets,
  onTicket,
  onReload,
}: {
  api: AtmanApi;
  project: string;
  lead: Lead | null;
  leadError: unknown;
  thread: Thread | null;
  threadError: unknown;
  knownTickets: ReadonlySet<string>;
  onTicket: (id: string) => void;
  onReload: () => void;
}) {
  const [older, setOlder] = useState<Post[]>([]);
  const [cursor, setCursor] = useState<string>("");
  const [moreLeft, setMoreLeft] = useState<boolean | null>(null);
  const [paging, setPaging] = useState(false);
  const [pageError, setPageError] = useState<string>("");
  const scroller = useRef<HTMLDivElement | null>(null);

  // A new project (or a new thread partner) throws away the pages we walked.
  useEffect(() => {
    setOlder([]);
    setCursor("");
    setMoreLeft(null);
    setPageError("");
  }, [project, thread?.with]);

  // The thread reads oldest-first, like a conversation, so it opens on the
  // newest message. Paging back keeps the reader where they were: the jump only
  // happens when a *newer* message arrives (a new id at the end), never when
  // older pages are prepended above.
  const newestId = thread?.messages.length ? thread.messages[thread.messages.length - 1].id : "";
  useEffect(() => {
    const el = scroller.current;
    if (!el || !newestId) return;
    el.scrollTop = el.scrollHeight;
  }, [newestId]);

  const loadOlder = useCallback(async () => {
    if (!thread) return;
    const before = cursor || thread.oldest_id;
    if (!before) return;
    setPaging(true);
    setPageError("");
    try {
      const page = await api.thread({ project, with: thread.with, before });
      setOlder((prev) => [...page.messages, ...prev]);
      setCursor(page.oldest_id);
      setMoreLeft(page.has_more);
    } catch (e) {
      setPageError(e instanceof Error ? e.message : String(e));
    } finally {
      setPaging(false);
    }
  }, [api, cursor, project, thread]);

  async function pickLead(seat: string) {
    try {
      await api.setLead(seat, project);
      onReload();
    } catch (e) {
      setPageError(e instanceof Error ? e.message : String(e));
    }
  }

  if (leadError) return <Failure what="The lead" error={leadError} />;
  if (!lead) return <p className="muted">Reading the lead…</p>;

  const cap = lead.status?.capability;
  const posts = [...older, ...(thread?.messages || [])];
  const hasMore = moreLeft === null ? !!thread?.has_more : moreLeft;

  return (
    <section className="pane pane-chat" aria-label="Chat with the lead">
      <header className="pane-head">
        <button
          type="button"
          className="skip"
          onClick={() => document.getElementById("composer-text")?.focus()}
        >
          Skip to the composer
        </button>
        <h2 className="pill">
          lead ·{" "}
          {lead.lead ? (
            <span data-testid="lead-name">
              {lead.lead}@{lead.project}
            </span>
          ) : (
            <Missing what="not picked" />
          )}
        </h2>
        {lead.status ? (
          <>
            <span className="harness" data-testid="lead-harness">
              {lead.status.harness || UNKNOWN}
            </span>
            <span className="muted" data-testid="lead-capability">
              {cap?.line}
            </span>
          </>
        ) : null}
      </header>

      {lead.needs_lead ? (
        <Picker lead={lead} onPick={pickLead} canWrite={!!lead.operator && !!api.writeToken} />
      ) : (
        <>
          <StatusStrip lead={lead} />
          <CannotAnswer lead={lead} />
          <div className="thread" ref={scroller} data-testid="thread">
            {threadError ? <Failure what="The thread" error={threadError} /> : null}
            {thread && !threadError ? (
              <>
                <div className="thread-top">
                  {hasMore ? (
                    <button type="button" className="btn-quiet" onClick={() => void loadOlder()} disabled={paging}>
                      {paging ? "reading older…" : "Load older messages"}
                    </button>
                  ) : (
                    <span className="muted">
                      {posts.length ? "start of the thread on this board" : "no messages with this seat yet"}
                    </span>
                  )}
                  <span className="muted">
                    {posts.length} shown{thread.archives ? " (archives included)" : ""}
                  </span>
                </div>
                {pageError ? (
                  <p className="failure" role="alert">
                    Older messages could not be read. {pageError}
                  </p>
                ) : null}
                <ul className="posts">
                  {posts.map((p) => (
                    <PostRow key={p.id || `${p.at}-${p.from}`} post={p} project={project} knownTickets={knownTickets} onTicket={onTicket} />
                  ))}
                </ul>
              </>
            ) : null}
          </div>
          {thread ? <Composer api={api} thread={thread} lead={lead} onPosted={onReload} /> : null}
        </>
      )}
    </section>
  );
}
