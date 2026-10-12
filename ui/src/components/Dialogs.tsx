/**
 * The dialogs (spec §6): Accept, Reject, Assign, and the sheets for the
 * actions the app does not run itself (Start an agent, Set objective, Create
 * ticket — a copyable command each).
 *
 * Accept shows the full 40-character head it will bind to, and nothing posts
 * until a re-read confirms that head is still the seat's current one (§6.2).
 * An error comes back in the API's words, with the button re-enabled; nothing
 * is retried on its own. No dialog offers "accept latest" or an accept
 * without notes; Reject needs a reason; Assign never picks a seat.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * The head the poll reports becomes a stale signal only when it changes after
 * the dialog opened: a re-read that already moved the dialog forward must not
 * be undone by a plan that has not caught up yet.
 */
function usePollHead(liveHead: string): string {
  const atOpen = useRef(liveHead);
  return liveHead !== atOpen.current ? liveHead : "";
}
import type { AgentRow, PlanNode } from "../api/types";
import { presenceOf, seatStateLine, short, bucketOf } from "../v3/model";
import { headOf, reviewNotesOf, type Screen } from "../v3/screen";
import { READ_ONLY_WHY } from "./Buttons";
import { CopyButton } from "./bits";

function Shell({ title, children, onClose, label }: { title: string; children: ReactNode; onClose: () => void; label?: string }) {
  const first = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = first.current?.querySelector<HTMLElement>("textarea, input, select, button.primary, button");
    el?.focus();
  }, []);
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <button type="button" className="scrim" aria-label="Close" onClick={onClose} />
      <div className="dlg" role="dialog" aria-modal="true" aria-label={label || title} ref={first} data-testid="dialog">
        <h3>{title}</h3>
        {children}
      </div>
    </>
  );
}

function Stale({ head, onAgain }: { head: string; onAgain: () => void }) {
  return (
    <div className="stale" data-testid="stale">
      <span className="t">
        Changed since you opened this: new head <code>{short(head)}</code>.
      </span>
      <button type="button" className="btn primary" onClick={onAgain}>
        Review again
      </button>
    </div>
  );
}

export interface VerdictDialogProps {
  node: PlanNode;
  s: Screen;
  /** The head the board reports now (from the poll), so a push while the dialog is open is caught. */
  liveHead: string;
  /** Re-read the ticket and return its current review head (§6.2). */
  checkHead: () => Promise<string>;
  submit: (head: string, text: string) => Promise<void>;
  onClose: () => void;
}

export function AcceptDialog(p: VerdictDialogProps) {
  const { node, s } = p;
  const [head, setHead] = useState(headOf(node));
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [newHead, setNewHead] = useState("");
  const owner = node.owner || "";
  const mine = !!owner && owner.toLowerCase() === s.operator.toLowerCase();
  const pollHead = usePollHead(p.liveHead);
  const stale = newHead && newHead !== head ? newHead : pollHead && pollHead !== head ? pollHead : "";
  const why = s.readOnly ? READ_ONLY_WHY : mine ? "You wrote this, so you can't accept it. Ask another seat." : !head ? "Nothing was submitted for review yet." : "";

  async function go() {
    if (why || busy) return;
    if (!notes.trim()) {
      setError("Notes are required: say what you checked and why it passes.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const current = await p.checkHead();
      if (current && current !== head) {
        setNewHead(current);
        return;
      }
      await p.submit(head, notes.trim());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title={`Accept ${node.id}`} onClose={p.onClose}>
      <p className="lede">
        {node.title}. Submitted by {owner || "?"}. Your accept binds to this exact head and nothing else.
      </p>
      <label htmlFor="accept-head">Review head</label>
      <div className="shabox" id="accept-head" data-testid="accept-head">
        {head || "none submitted"}
      </div>
      <label>What the seat said to look at</label>
      <p className="notes">{reviewNotesOf(node) || <span className="muted">No review notes on this head.</span>}</p>
      <label htmlFor="accept-notes">Notes (why this is accepted)</label>
      <textarea id="accept-notes" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="What you checked and why it passes" disabled={!!why || !!stale || busy} />
      {stale ? (
        <>
          <Stale
            head={stale}
            onAgain={() => {
              setHead(stale);
              setNewHead("");
              setError("");
            }}
          />
          <div className="foot">
            <button type="button" className="btn" onClick={p.onClose}>
              Cancel
            </button>
          </div>
        </>
      ) : (
        <div className="foot">
          {why ? (
            <span className="why" data-testid="why">
              {why}
            </span>
          ) : null}
          <button type="button" className="btn" onClick={p.onClose}>
            Cancel
          </button>
          <button type="button" className="btn primary" disabled={!!why || busy} onClick={() => void go()} data-testid="accept-submit">
            {busy ? <span className="spin" aria-hidden="true" /> : null}
            Accept <span className="sha">{short(head)}</span>
          </button>
        </div>
      )}
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      {s.details ? (
        <p className="details">
          re-read GET /api/v1/ticket/{node.id} → review.head, then POST /api/v1/ticket/{node.id}/accept {"{sha, notes}"} ·{" "}
          <code>
            atm accept {node.id} --sha {head} --notes "…"
          </code>
        </p>
      ) : null}
    </Shell>
  );
}

export function RejectDialog(p: VerdictDialogProps) {
  const { node, s } = p;
  const [head, setHead] = useState(headOf(node));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [newHead, setNewHead] = useState("");
  const owner = node.owner || "";
  const mine = !!owner && owner.toLowerCase() === s.operator.toLowerCase();
  const pollHead = usePollHead(p.liveHead);
  const stale = newHead && newHead !== head ? newHead : pollHead && pollHead !== head ? pollHead : "";
  const why = s.readOnly ? READ_ONLY_WHY : mine ? "You wrote this; a reviewer rejects it, not you." : !head ? "Nothing was submitted for review yet." : "";

  async function go() {
    if (why || busy) return;
    if (!reason.trim()) {
      setError("A reason is required: what is wrong and what would pass.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const current = await p.checkHead();
      if (current && current !== head) {
        setNewHead(current);
        return;
      }
      await p.submit(head, reason.trim());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title={`Reject ${node.id}`} onClose={p.onClose}>
      <p className="lede">
        {node.title}. Goes back to {owner || "the author"} as claimed, with your reason.
      </p>
      <label htmlFor="reject-head">Review head</label>
      <div className="shabox" id="reject-head">
        {head || "none submitted"}
      </div>
      <label htmlFor="reject-reason">Reason (required)</label>
      <textarea id="reject-reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="What is wrong and what would pass" disabled={!!why || !!stale || busy} />
      {stale ? (
        <>
          <Stale
            head={stale}
            onAgain={() => {
              setHead(stale);
              setNewHead("");
              setError("");
            }}
          />
          <div className="foot">
            <button type="button" className="btn" onClick={p.onClose}>
              Cancel
            </button>
          </div>
        </>
      ) : (
        <div className="foot">
          {why ? (
            <span className="why" data-testid="why">
              {why}
            </span>
          ) : null}
          <button type="button" className="btn" onClick={p.onClose}>
            Cancel
          </button>
          <button type="button" className="btn danger" disabled={!!why || busy} onClick={() => void go()} data-testid="reject-submit">
            {busy ? <span className="spin" aria-hidden="true" /> : null}
            Reject and return to {owner || "the author"}
          </button>
        </div>
      )}
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      {s.details ? (
        <p className="details">
          POST /api/v1/ticket/{node.id}/reject {"{sha, reason}"} ·{" "}
          <code>
            atm reject {node.id} --sha {head} --reason "…"
          </code>
        </p>
      ) : null}
    </Shell>
  );
}

export function AssignDialog({
  id,
  s,
  candidates,
  submit,
  onClose,
}: {
  /** The ticket, or "" to pick one from Up next. */
  id: string;
  s: Screen;
  candidates: ReadonlyArray<PlanNode>;
  submit: (ticket: string, seat: string, why: string) => Promise<void>;
  onClose: () => void;
}) {
  const node = id ? s.nodes.get(id) || null : null;
  const [ticket, setTicket] = useState(id || candidates[0]?.id || "");
  const [seat, setSeat] = useState("");
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [asked, setAsked] = useState(false);
  const nodes = Array.from(s.nodes.values());
  const seats = s.agents.filter((a) => a.name.toLowerCase() !== s.operator.toLowerCase());
  const reassign = !!node?.owner;

  async function go() {
    if (s.readOnly || busy) return;
    if (!seat) {
      setAsked(true);
      return;
    }
    if (!ticket) {
      setError("Pick a ticket first.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await submit(ticket, seat, why.trim());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title={`${reassign ? "Reassign" : "Assign"} ${id || "a ticket"}`} onClose={onClose}>
      <p className="lede">
        {node ? `${node.title}. ` : ""}Claims it on the seat's behalf. Nothing is picked for you.
      </p>
      {id ? null : (
        <>
          <label htmlFor="assign-ticket">Ticket</label>
          <select id="assign-ticket" value={ticket} onChange={(e) => setTicket(e.target.value)}>
            {candidates.length ? null : <option value="">No ticket is queued</option>}
            {candidates.map((n) => (
              <option key={n.id} value={n.id}>
                {n.id} · {n.title}
              </option>
            ))}
          </select>
        </>
      )}
      <label>Seat</label>
      {seats.length ? (
        seats.map((a: AgentRow) => {
          const pr = presenceOf(a, nodes);
          const warn = pr.kind !== "working" && pr.kind !== "idle";
          return (
            <label key={a.name} className="seat-row">
              <input type="radio" name="seat" value={a.name} checked={seat === a.name} onChange={() => setSeat(a.name)} />
              <span className={`dot ${pr.kind}`} aria-hidden="true" />
              <span>{a.name}</span>
              <span className={`st${warn ? " warn" : ""}`}>
                {seatStateLine(a, nodes)}
                {warn ? " · will not start until then" : ""}
              </span>
            </label>
          );
        })
      ) : (
        <p className="muted">No seats are registered on this board. Register one with <code>atm join &lt;name&gt;</code>.</p>
      )}
      <label htmlFor="assign-why">Why (optional)</label>
      <input id="assign-why" type="text" value={why} onChange={(e) => setWhy(e.target.value)} placeholder="Appended to the ticket's note" />
      <div className="foot">
        {s.readOnly ? <span className="why">{READ_ONLY_WHY}</span> : asked && !seat ? <span className="why" data-testid="why">Pick a seat first. The app never picks one for you.</span> : null}
        <button type="button" className="btn" onClick={onClose}>
          Cancel
        </button>
        <button type="button" className="btn primary" disabled={s.readOnly || busy} onClick={() => void go()} data-testid="assign-submit">
          {busy ? <span className="spin" aria-hidden="true" /> : null}
          {seat ? `Assign to ${seat}` : "Assign"}
        </button>
      </div>
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      {s.details ? (
        <p className="details">
          POST /api/v1/ticket/{ticket || "<id>"}/assign {"{owner, notes}"} ·{" "}
          <code>
            atm assign {ticket || "<id>"} --owner {seat || "<seat>"} --notes "…"
          </code>
        </p>
      ) : null}
    </Shell>
  );
}

/** The one action the app does not run (§6.4): a spawn carries the operator's auth. */
export function StartSheet({ s, seat, onClose }: { s: Screen; seat?: string; onClose: () => void }) {
  const agents = s.agents.filter((a) => a.name.toLowerCase() !== s.operator.toLowerCase());
  const [who, setWho] = useState(seat || agents[0]?.name || "");
  const a = agents.find((x) => x.name === who);
  const cmd = who ? `atm spawn ${who} --persist` : "atm join <name>   # register a seat first";
  return (
    <Shell title="Start an agent" onClose={onClose}>
      <p className="lede">Runs a harness on your machine with your credentials, so the app does not start it for you. Paste this in a terminal.</p>
      <label htmlFor="start-seat">Seat</label>
      {agents.length ? (
        <select id="start-seat" value={who} onChange={(e) => setWho(e.target.value)}>
          {agents.map((x) => (
            <option key={x.name} value={x.name}>
              {x.name} · {x.harness || "harness not recorded"}
            </option>
          ))}
        </select>
      ) : (
        <p className="muted">No seat is registered on this board yet.</p>
      )}
      {a ? (
        <p className="muted" style={{ marginTop: 6 }}>
          {a.name} · {a.harness || "harness not recorded"} (as registered with <code>atm join</code>)
        </p>
      ) : null}
      <div className="cmd">
        <code>{cmd}</code>
        <CopyButton text={cmd} />
      </div>
      <div className="foot">
        <button type="button" className="btn" onClick={onClose}>
          Done
        </button>
      </div>
    </Shell>
  );
}

/** Set objective… / Create ticket… (§6.6): the exact command, since the API has no route for either. */
export function CommandSheet({ kind, onClose }: { kind: "objective" | "create"; onClose: () => void }) {
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const q = (t: string) => `"${t.replace(/"/g, '\\"')}"`;
  const cmd =
    kind === "objective"
      ? `atm objective --set ${q(a || "…")} --exit ${q(b || "…")}`
      : `atm create ${q(a || "…")} --role ${b || "backend"}`;
  return (
    <Shell title={kind === "objective" ? "Set the objective" : "Create a ticket"} onClose={onClose}>
      <p className="lede">
        {kind === "objective"
          ? "One sentence for what this board is for, and how you will know it is done. The app has no route for this yet; it hands you the command."
          : "What has to be true when this is done. The app has no route for this yet; it hands you the command."}
      </p>
      <label htmlFor="cs-a">{kind === "objective" ? "Objective" : "Title"}</label>
      <input id="cs-a" type="text" value={a} onChange={(e) => setA(e.target.value)} placeholder={kind === "objective" ? "Ship a small stats API with a screen on top" : "What has to be true when this is done"} />
      <label htmlFor="cs-b">{kind === "objective" ? "Done when" : "Role"}</label>
      <input id="cs-b" type="text" value={b} onChange={(e) => setB(e.target.value)} placeholder={kind === "objective" ? "the screen shows live stats from the API, accepted by a second seat" : "backend"} />
      <div className="cmd">
        <code>{cmd}</code>
        <CopyButton text={cmd} />
      </div>
      <div className="foot">
        <button type="button" className="btn" onClick={onClose}>
          Done
        </button>
      </div>
    </Shell>
  );
}

/** Up-next candidates for the empty Working section's Assign… */
export function assignable(nodes: ReadonlyArray<PlanNode>): PlanNode[] {
  return nodes.filter((n) => bucketOf(n) === "next" && n.phase !== "reserved");
}
