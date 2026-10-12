/**
 * The sections (spec §5.4, §5.5, §5.10) and the ticket card (§2, §5.3, §6).
 *
 * Order is fixed: Needs you · Working · Blocked · Up next · Done. A section
 * with nothing in it collapses to its one-line empty copy. Each card is the
 * id and title, exactly one sentence in plain words, and the verbs that apply
 * to it; after a verb runs, the row is the result line the API returned.
 */

import type { ReactNode } from "react";
import type { NeedsYouItem, PlanNode } from "../api/types";
import { acceptanceProofText } from "../lib/map";
import {
  bucketOf,
  blockReason,
  needsKindOf,
  relTime,
  sentenceOf,
  short,
  type Bucket,
  type NeedsFilter,
} from "../v3/model";
import { headOf, reviewNotesOf, type Screen } from "../v3/screen";
import { RichText } from "./Badge";
import { ActionRow, Result, type Verb } from "./Buttons";

const SELF_WHY = "You wrote this, so you can't accept it.";
const NO_HEAD_WHY = "Nothing was submitted for review yet.";

/** The verbs a card (or its panel) carries, by section (§6). */
export function verbsFor(node: PlanNode, s: Screen): Verb[] {
  const owner = (node.owner || "").trim();
  const mine = !!owner && !!s.operator && owner.toLowerCase() === s.operator.toLowerCase();
  const to = owner && !mine ? owner : "everyone";
  const head = headOf(node);
  const message: Verb = { label: "Message", onClick: () => s.message(to, node.id), kind: "" };
  const answer: Verb = { label: "Answer", onClick: () => s.message(to, node.id), kind: "primary" };
  const assign = (label: string, kind: Verb["kind"] = ""): Verb => ({ label, onClick: () => s.act({ kind: "assign", id: node.id }), kind });
  switch (bucketOf(node)) {
    case "needs": {
      const why = mine ? SELF_WHY : !head ? NO_HEAD_WHY : "";
      return [
        {
          label: (
            <>
              Accept{head ? <span className="sha"> {short(head)}</span> : null}
            </>
          ),
          name: head ? `Accept ${short(head)}` : "Accept",
          onClick: () => s.act({ kind: "accept", id: node.id }),
          kind: "primary",
          why,
          testId: "accept",
        },
        { label: "Reject…", onClick: () => s.act({ kind: "reject", id: node.id }), kind: "danger", why, testId: "reject" },
        message,
      ];
    }
    case "working":
      return [message];
    case "blocked":
      if (node.phase === "blocked") return [answer, assign("Reassign…")];
      if (node.phase === "hold" || node.phase === "capture") return [message];
      return [assign("Reassign…", "primary"), message];
    case "next":
      return [assign("Assign…"), { ...message, kind: "quiet" }];
    default:
      return [];
  }
}

function Details({ node, s }: { node: PlanNode; s: Screen }) {
  if (!s.details) return null;
  const head = headOf(node);
  return (
    <div className="details" data-testid="card-details">
      phase {node.phase || "?"}
      {node.evidence ? ` · ${node.evidence}` : ""}
      {head ? (
        <>
          <br />
          review head <code>{head}</code>
          {node.artifact?.branch ? (
            <>
              {" "}
              · branch <code>{node.artifact.branch}</code>
            </>
          ) : null}
          {node.artifact?.pr ? ` · PR ${node.artifact.pr}` : ""}
          <br />
          diff: <code>git diff main...{head}</code> · copy and run it in the repo; the app never runs git on a read
        </>
      ) : null}
      {node.wait?.cmd ? (
        <>
          <br />
          <code>{String(node.wait.cmd)}</code>
        </>
      ) : null}
      <br />
      <code>atm show {node.id}</code>
    </div>
  );
}

export function TicketCard({ node, s }: { node: PlanNode; s: Screen }) {
  const b = bucketOf(node);
  const result = s.results.get(node.id);
  const sentence = sentenceOf(node, { operator: s.operator, nodes: Array.from(s.nodes.values()), now: s.now });
  const head = headOf(node);
  const notes = reviewNotesOf(node);
  const proof =
    b === "done" && node.accepted
      ? acceptanceProofText({ id: node.id, accepted: true, acceptance: node.acceptance, review: node.review as never }).text
      : "";
  return (
    <article
      className={`card ${b}`}
      data-testid="card"
      data-ticket={node.id}
      data-bucket={b}
      onClick={(e) => {
        const el = e.target as HTMLElement;
        if (el.closest("button, select, a, textarea, input, .actions, .details")) return;
        s.open(node.id);
      }}
    >
      <div className="head">
        <span className="id">{node.id}</span>
        <span className="title">{node.title}</span>
        <button type="button" className="open" onClick={() => s.open(node.id)} aria-label={`Open ${node.id}`}>
          Open ›
        </button>
      </div>
      <p className="sentence" data-testid="sentence">
        {sentence}
      </p>
      {b === "needs" && head ? (
        <p className="sub" data-testid="review-line">
          Review head <code>{short(head)}</code>
          {notes ? ` · ${notes}` : ""}
        </p>
      ) : null}
      {proof ? (
        <p className="sub" data-testid="acceptance-proof">
          Proof · {proof}
        </p>
      ) : null}
      {result ? <Result line={result} /> : <ActionRow verbs={verbsFor(node, s)} readOnly={s.readOnly} />}
      <Details node={node} s={s} />
    </article>
  );
}

/* ----------------------------------------------------------- needs-you items */

function NeedItem({ item, s }: { item: NeedsYouItem; s: Screen }) {
  const result = s.results.get(item.id);
  const when = relTime(item.at, s.now);
  const re = item.re;
  const node = re ? s.nodes.get(re) : undefined;
  const open: Verb | null = node ? { label: `Open ${re}`, onClick: () => s.open(re), kind: "quiet" } : null;
  const reassign: Verb | null = node ? { label: "Reassign…", onClick: () => s.act({ kind: "assign", id: re }), kind: "" } : null;
  let line: ReactNode;
  let verbs: Verb[] = [];
  if (item.kind === "escalated") {
    line = (
      <>
        Automated step <RichText text={item.id} nodes={s.nodes} onOpen={s.open} /> escalated: {item.text}
      </>
    );
    const why = `Keep and Remove are not wired in the app yet. Run atm show ${item.id} in a terminal.`;
    verbs = [
      { label: "Keep", onClick: () => undefined, kind: "", why },
      { label: "Remove", onClick: () => undefined, kind: "", why },
    ];
  } else if (item.kind === "seat") {
    line = (
      <>
        <RichText text={item.text} nodes={s.nodes} onOpen={s.open} />
      </>
    );
    const start: Verb = { label: item.why === "limited until" ? "Start another agent…" : "Start…", onClick: () => s.act({ kind: "start", seat: item.from }), kind: "" };
    verbs = /^limited/.test(item.why)
      ? [reassign ? { ...reassign, kind: "primary" } : null, { ...start, label: "Start another agent…" }, open].filter(Boolean) as Verb[]
      : [{ ...start, kind: "primary" }, reassign].filter(Boolean) as Verb[];
  } else if (/stuck/i.test(item.why)) {
    line = (
      <>
        <span className="who">{item.from}</span> has been stuck{re ? <> on <RichText text={re} nodes={s.nodes} onOpen={s.open} /></> : null}
        {when ? ` since ${when.replace(/ ago$/, " ago")}` : ""}: “<RichText text={item.text.replace(/^stuck:\s*/i, "")} nodes={s.nodes} onOpen={s.open} />”
      </>
    );
    verbs = [{ label: "Answer", onClick: () => s.message(item.from, re), kind: "primary" }, reassign, open].filter(Boolean) as Verb[];
  } else {
    line = (
      <>
        <span className="who">{item.from}</span> asked{when ? ` ${when}` : ""}
        {re ? <> about <RichText text={re} nodes={s.nodes} onOpen={s.open} /></> : null}: “<RichText text={item.text} nodes={s.nodes} onOpen={s.open} />”
      </>
    );
    verbs = [{ label: "Answer", onClick: () => s.message(item.from, re), kind: "primary" }, open].filter(Boolean) as Verb[];
  }
  return (
    <div className="item" data-testid="need-item" data-kind={item.kind}>
      <p className="text">{line}</p>
      {result ? <Result line={result} /> : <ActionRow verbs={verbs} readOnly={s.readOnly} />}
      {s.details ? (
        <div className="details">
          {item.label} · {item.why}
          {item.id && !item.id.startsWith("seat:") ? ` · ${item.id}` : ""}
        </div>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ sections */

function Section({
  id,
  title,
  n,
  children,
  empty,
  emptyActions,
  header,
}: {
  id: string;
  title: ReactNode;
  n: number;
  children?: ReactNode;
  empty: ReactNode;
  emptyActions?: ReactNode;
  header?: ReactNode;
}) {
  return (
    <section className="section" id={`sec-${id}`} aria-label={typeof title === "string" ? title : id} data-testid={`section-${id}`}>
      <h2>
        {title} <span className="n">({n})</span>
        {header}
      </h2>
      {n ? (
        <div className="cards">{children}</div>
      ) : (
        <div className="empty" data-testid={`empty-${id}`}>
          <span className="grow">{empty}</span>
          {emptyActions}
        </div>
      )}
    </section>
  );
}

const FILTERS: Array<[NeedsFilter, string]> = [
  ["all", "All"],
  ["mentions", "Mentions"],
  ["accept", "Accept"],
  ["blocked", "Blocked"],
];

export function NeedsYouSection({
  items,
  reviews,
  filter,
  onFilter,
  s,
}: {
  items: ReadonlyArray<NeedsYouItem> | null;
  reviews: ReadonlyArray<PlanNode>;
  filter: NeedsFilter;
  onFilter: (f: NeedsFilter) => void;
  s: Screen;
}) {
  const reviewIds = new Set(reviews.map((n) => n.id));
  // A review item whose ticket is on the plan is drawn as the ticket card, with
  // its Accept / Reject; the item line is only for a ticket the plan lacks.
  const rows: Array<{ k: Exclude<NeedsFilter, "all">; at: string; el: ReactNode }> = [];
  for (const n of reviews) rows.push({ k: "accept", at: n.last_note?.at || "", el: <TicketCard key={n.id} node={n} s={s} /> });
  for (const it of items || []) {
    if (it.kind === "review" && reviewIds.has(it.id)) continue;
    rows.push({ k: needsKindOf(it), at: it.at, el: <NeedItem key={`${it.kind}:${it.id}`} item={it} s={s} /> });
  }
  rows.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
  const n = rows.length;
  const count = (k: NeedsFilter) => (k === "all" ? n : rows.filter((r) => r.k === k).length);
  const shown = rows.filter((r) => filter === "all" || r.k === filter);
  const word = FILTERS.find(([k]) => k === filter)?.[1] || "";
  const chips = n ? (
    <span className="chips" role="group" aria-label="Needs you filters">
      {FILTERS.map(([k, label]) => (
        <button key={k} type="button" className="filter-chip" aria-pressed={filter === k} aria-label={`${label} (${count(k)})`} onClick={() => onFilter(k)}>
          {label}
          <span className="n">{count(k)}</span>
        </button>
      ))}
    </span>
  ) : null;
  return (
    <section className="section" id="sec-needs" aria-label="Needs you" data-testid="section-needs">
      <h2>
        Needs you <span className="n">({n})</span>
        {chips}
      </h2>
      {items === null && !reviews.length ? (
        <div className="empty">
          <span className="grow missing">The needs-you queue could not be read.</span>
        </div>
      ) : !n ? (
        <div className="empty" data-testid="empty-needs">
          <span className="grow">Nothing needs you right now.</span>
        </div>
      ) : shown.length ? (
        <div className="cards">{shown.map((r) => r.el)}</div>
      ) : (
        <div className="empty" data-testid="empty-needs">
          <span className="grow">Nothing under {word}.</span>
        </div>
      )}
    </section>
  );
}

export function WorkingSection({ nodes, s }: { nodes: ReadonlyArray<PlanNode>; s: Screen }) {
  const assign: Verb = { label: "Assign…", onClick: () => s.act({ kind: "assign", id: "" }), kind: "primary" };
  const start: Verb = { label: "Start an agent…", onClick: () => s.act({ kind: "start" }), kind: "quiet" };
  return (
    <Section
      id="working"
      title="Working"
      n={nodes.length}
      empty="Nobody is working. Assign a ticket or start an agent."
      emptyActions={<ActionRow verbs={[assign, start]} readOnly={s.readOnly} />}
    >
      {nodes.map((n) => (
        <TicketCard key={n.id} node={n} s={s} />
      ))}
    </Section>
  );
}

export function BlockedSection({ nodes, s }: { nodes: ReadonlyArray<PlanNode>; s: Screen }) {
  return (
    <Section id="blocked" title="Blocked" n={nodes.length} empty="Nothing is blocked.">
      {nodes.map((n) => (
        <TicketCard key={n.id} node={n} s={s} />
      ))}
    </Section>
  );
}

export function UpNextSection({ nodes, s }: { nodes: ReadonlyArray<PlanNode>; s: Screen }) {
  return (
    <Section id="next" title="Up next" n={nodes.length} empty="Nothing is queued.">
      {nodes.map((n) => (
        <TicketCard key={n.id} node={n} s={s} />
      ))}
    </Section>
  );
}

export function DoneSection({ nodes, s, open, onToggle }: { nodes: ReadonlyArray<PlanNode>; s: Screen; open: boolean; onToggle: () => void }) {
  const n = nodes.length;
  return (
    <section className={`section${open ? " open" : ""}`} id="sec-done" aria-label="Done" data-testid="section-done">
      <h2>
        <button type="button" className="fold" aria-expanded={open} onClick={onToggle}>
          Done <span className="n">({n})</span>
        </button>
        {open ? null : <span className="collapsed-word">collapsed</span>}
      </h2>
      {open ? (
        n ? (
          <div className="cards">
            {nodes.map((node) => (
              <TicketCard key={node.id} node={node} s={s} />
            ))}
          </div>
        ) : (
          <div className="empty" data-testid="empty-done">
            <span className="grow">Nothing accepted yet.</span>
          </div>
        )
      ) : null}
    </section>
  );
}

/** Group the plan's steps by section, in the plan's own order. */
export function groupNodes(nodes: ReadonlyArray<PlanNode>): Record<Bucket, PlanNode[]> {
  const by: Record<Bucket, PlanNode[]> = { needs: [], working: [], blocked: [], next: [], done: [] };
  for (const n of nodes) by[bucketOf(n)].push(n);
  return by;
}

export { blockReason };
