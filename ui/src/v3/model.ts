/**
 * The one-screen view's pure model (docs/design/app-v3.md).
 *
 * Every sentence an operator reads on a card, every presence dot and every
 * count is made here from the API's records, as a function of its inputs, so
 * the tests can hold each one to the spec's table (§5.3, §5.8, §3) without a
 * browser. Nothing here invents a time, a name or an accept: a field the board
 * never recorded gives the shorter sentence, never a guess.
 */

import type { AgentRow, NeedsYouItem, Plan, PlanNode } from "../api/types";
import { parseStamp } from "../lib/format";

/* --------------------------------------------------------------- time words */

/** "5 min ago", "2 h ago", or a date once it is more than a day old (§5). */
export function relHours(h: number | null | undefined, now: Date = new Date()): string {
  if (h === null || h === undefined || !Number.isFinite(h)) return "";
  if (h < 1 / 60) return "just now";
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min ago`;
  if (h < 24) return `${h < 10 ? Math.round(h * 10) / 10 : Math.round(h)} h ago`;
  const d = new Date(now.getTime() - h * 3600 * 1000);
  return `on ${d.toLocaleDateString(undefined, { day: "numeric", month: "short" })}`;
}

/** The same words from a stamp; "" when the board recorded none. */
export function relTime(iso: string | null | undefined, now: Date = new Date()): string {
  const d = parseStamp(iso);
  if (!d) return "";
  return relHours((now.getTime() - d.getTime()) / 3600000, now);
}

/** "12 min", "2 h 5 min" for an elapsed run (§5.3); "" when not recorded. */
export function elapsedWords(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "";
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
}

export const short = (sha: string | null | undefined): string => (sha || "").trim().slice(0, 7);

/* ------------------------------------------------------------------ buckets */

export type Bucket = "needs" | "working" | "blocked" | "next" | "done";

const SEAT_BLOCKERS = new Set(["seat_limited", "seat_offline", "auth"]);

function blockers(node: PlanNode): PlanNode["blockers"] {
  return Array.isArray(node.blockers) ? node.blockers : [];
}

function seatBlocker(node: PlanNode) {
  return blockers(node).find((b) => SEAT_BLOCKERS.has(b.kind)) || null;
}

/** Which section a step belongs to (§3). The order of the sections never changes. */
export function bucketOf(node: PlanNode): Bucket {
  const phase = node.phase || "";
  if (phase === "review") return "needs";
  if (node.status === "done" || phase === "done" || phase === "discarded") return "done";
  if (phase === "blocked" || phase === "hold" || phase === "capture") return "blocked";
  if (seatBlocker(node)) return "blocked";
  if (phase === "working" || phase === "posted") return "working";
  return "next";
}

/* ----------------------------------------------------------- one sentence */

export interface SentenceCtx {
  operator: string;
  nodes: ReadonlyArray<PlanNode>;
  now?: Date;
}

function byId(nodes: ReadonlyArray<PlanNode>): Map<string, PlanNode> {
  return new Map(nodes.map((n) => [n.id, n]));
}

/** "Accepted by @boss on 3f2a1c0" -> {by, sha}; null for anything else. */
export function acceptedLabel(label: string | undefined): { by: string; sha: string } | null {
  const m = /^Accepted by @(\S+) on ([0-9a-fA-F]{7,40}|unrecorded artifact)/.exec(label || "");
  return m ? { by: m[1], sha: m[2] } : null;
}

/** The T-1459 sentence for an accept that is not bound to a review head. */
export function unboundAcceptSentence(id: string, by: string, sha: string): string {
  return (
    `accept by @${by} on ${short(sha) || "?"} is not bound to a review head: ` +
    `run atm reopen ${id} --notes "...", then claim, then atm review, then atm accept --sha <new head>`
  );
}

function quote(text: string): string {
  const t = (text || "").trim().replace(/\s+/g, " ");
  return t.length > 160 ? t.slice(0, 157) + "…" : t;
}

/** The reason a blocked step gives, in the owner's words: the last note minus the CLI prefix. */
export function blockReason(node: PlanNode): string {
  const text = (node.last_note?.text || "").trim();
  return quote(text.replace(/^(blocked|block|stuck)\s*:\s*/i, ""));
}

function untilOf(text: string): string {
  const m = /limited until (.+)$/.exec(text || "");
  return m ? m[1].trim() : "";
}

/**
 * Exactly one sentence per card (§5.3). The spec's table, row by row. A row's
 * time words come from the record that row names; when that record is absent
 * the sentence is shorter rather than wrong.
 */
export function sentenceOf(node: PlanNode, ctx: SentenceCtx): string {
  const now = ctx.now || new Date();
  const owner = (node.owner || "").trim();
  const mine = !!owner && !!ctx.operator && owner.toLowerCase() === ctx.operator.toLowerCase();
  const phase = node.phase || "";
  // The Work payload records since_update_h for working steps; elsewhere the
  // last note's own stamp is the record, and with neither the sentence is shorter.
  const since = relHours(node.since_update_h, now) || relTime(node.last_note?.at, now);
  const seat = seatBlocker(node);
  const nodes = byId(ctx.nodes);

  if (phase === "review") {
    const acc = acceptedLabel(node.review?.label);
    if (acc && !mine) return `Accepted by ${acc.by}${since ? ` ${since}` : ""}. Waiting for merge.`;
    if (mine) return `You finished it${since ? ` ${since}` : ""}. Another seat has to accept it.`;
    return `${owner || "A seat"} finished it${since ? ` ${since}` : ""}. Waiting for your accept.`;
  }
  if (node.status === "done" || phase === "done") {
    const acc = acceptedLabel(node.review?.label);
    const when = relTime(node.done_at, now);
    if (node.accepted && acc) return `Accepted by ${acc.by}${when ? ` ${when}` : ""} on ${short(acc.sha)}.`;
    if (node.accepted) return `Accepted${when ? ` ${when}` : ""}.`;
    if (acc) return unboundAcceptSentence(node.id, acc.by, acc.sha);
    if (node.released) return `Marked done by ${owner || "?"}${when ? ` ${when}` : ""}. Released by override, not accepted.`;
    return `Marked done by ${owner || "?"}${when ? ` ${when}` : ""}. Not accepted yet.`;
  }
  if (phase === "discarded") return `Discarded${relTime(node.done_at, now) ? ` ${relTime(node.done_at, now)}` : ""}.`;
  if (seat) {
    const who = owner || seat.on || "The seat";
    if (seat.kind === "seat_limited") {
      const until = untilOf(seat.text);
      return `${who} is limited${until ? ` until ${until}` : ""}. Nothing runs on it until then.`;
    }
    if (seat.kind === "auth") return `${who} is logged out. Run ${seat.cmd || `atm harness auth ${who}`}.`;
    return `${who} has no live session. It picks this up when started.`;
  }
  if (phase === "blocked") {
    const by = node.last_note?.by || owner || "?";
    const when = relTime(node.last_note?.at, now) || since;
    const reason = blockReason(node);
    return `Blocked by ${by}${when ? ` ${when}` : ""}${reason ? `: “${reason}”` : "."}`;
  }
  if (phase === "hold") {
    const reason = quote(String(node.wait?.text || "").replace(/^HOLD:?\s*/i, ""));
    return reason ? `On hold: ${reason}.` : "On hold.";
  }
  if (phase === "capture") return "Waiting for capture: run atm sound.";
  if (phase === "working") {
    const rejected = /^earlier REJECT by @(\S+) on (\S+)/.exec(node.review?.label || "");
    if (rejected) return `Rejected by ${rejected[1]} on ${short(rejected[2])}. Back with ${owner || "the author"}.`;
    const elapsed = elapsedWords(node.running?.elapsed_s);
    if (node.running && elapsed) return `${owner || node.running.seat || "A seat"} has it, running for ${elapsed}.`;
    if (node.stale) return `${owner || "A seat"} has it but has been silent for ${since.replace(/ ago$/, "") || "a while"}.`;
    return `${owner || "A seat"} has it${since ? `, last update ${since}` : ""}.`;
  }
  if (phase === "posted") {
    const age = relHours(node.dispatch?.age_h, now);
    return `Posted to the board${age ? ` ${age}` : ""}. Nobody has picked it up.`;
  }
  if (phase === "reserved") {
    const who = node.reserved_for || "a seat";
    return `Reserved for ${who}. Starts when ${who} wakes.`;
  }
  if (phase === "waiting") {
    const on = (Array.isArray(node.wait?.on) ? (node.wait!.on as string[]) : node.deps || [])[0] || "";
    const dep = on ? nodes.get(on) : undefined;
    if (dep?.phase === "review") return `Nobody has it. Waits on ${on}, which is in review.`;
    if (dep?.phase === "blocked") return `Nobody has it. Waits on ${on}, which is blocked.`;
    if (dep && (dep.status === "done" || dep.phase === "done") && !dep.accepted)
      return `Waits on ${on}, which is done but not accepted.`;
    return on ? `Nobody has it. Waits on ${on}.` : "Nobody has it. Waits on a dependency.";
  }
  return "Nobody has it. Ready to start.";
}

/** The phase word a ticket badge carries (§5.9). */
export function phaseWord(node: PlanNode): string {
  const phase = node.phase || "";
  if (phase === "review") return "in review";
  if (node.status === "done" || phase === "done") return node.accepted ? "accepted" : "done, not accepted";
  if (phase === "working" || phase === "posted") return "working";
  if (seatBlocker(node)) return seatBlocker(node)!.kind === "seat_limited" ? "seat limited" : "seat offline";
  if (phase === "waiting") {
    const on = (Array.isArray(node.wait?.on) ? (node.wait!.on as string[]) : node.deps || [])[0];
    return on ? `waits on ${on}` : "waiting";
  }
  return phase || "open";
}

/* ---------------------------------------------------------------- presence */

export type PresenceKind = "working" | "idle" | "offline" | "limited" | "logged-out" | "unknown";

export interface Presence {
  kind: PresenceKind;
  /** The words after the seat name, in a few words. */
  words: string;
  /** The hover reason for a non-working dot; "" for working. */
  why: string;
}

const AUTH_BLOCKING = new Set(["login_required", "expired"]);
const OFFLINE_STATES = new Set(["offline", "queued-offline", "recovery-required"]);

function seenWords(a: AgentRow): string {
  const h = a.seen_h;
  if (h === null || h === undefined) return "never";
  return relHours(h).replace(/ ago$/, "");
}

/** The step a seat is on now: the node it owns that is running, else the one its record names. */
export function runningNodeOf(seat: string, nodes: ReadonlyArray<PlanNode>, hint = ""): PlanNode | null {
  const own = nodes.filter((n) => (n.owner || "") === seat && (n.phase === "working" || n.phase === "review"));
  return own.find((n) => !!n.running) || own.find((n) => n.id === hint) || own.find((n) => n.phase === "working") || null;
}

/** "writing tests for T-002": the running ticket's latest update, first clause, never generated (§5.8). */
export function doingNow(seat: string, nodes: ReadonlyArray<PlanNode>, hint = ""): string {
  const node = runningNodeOf(seat, nodes, hint);
  if (!node) return hint ? `running ${hint}` : "working";
  const note = (node.last_note?.text || "").trim();
  const update = /^(update|started|progress)\s*:\s*/i.test(note) ? note.replace(/^(update|started|progress)\s*:\s*/i, "") : note;
  const own = node.last_note?.by === seat || !node.last_note?.by;
  if (!update || !own || /^(assign|claim|REVIEW)\b/i.test(update)) return `running ${node.id}, no update yet`;
  const clause = update.split(/[;:.\n]/)[0].trim();
  return clause ? `${clause.length > 60 ? clause.slice(0, 57) + "…" : clause} for ${node.id}` : `running ${node.id}, no update yet`;
}

/** Six visuals, six states (§5.8): working, idle, host offline, limited, logged out, unknown. */
export function presenceOf(a: AgentRow, nodes: ReadonlyArray<PlanNode> = []): Presence {
  const state = (a.state || "").toLowerCase();
  if (a.limit || state === "limited") {
    const until = a.limit_until || "reset unknown";
    return { kind: "limited", words: `limited until ${until}`, why: `Provider quota; resets ${until}. Nothing runs on it until then.` };
  }
  if (AUTH_BLOCKING.has(a.auth_surface?.state || "")) {
    const cmd = a.auth_surface?.recovery?.cmd || `atm harness auth ${a.name}`;
    return { kind: "logged-out", words: "logged out", why: cmd };
  }
  const running = runningNodeOf(a.name, nodes, a.ticket || "");
  if (state === "busy" || state === "working" || (running && running.running)) {
    return { kind: "working", words: doingNow(a.name, nodes, a.ticket || ""), why: "" };
  }
  if (a.reachable === false && (OFFLINE_STATES.has(a.adapter_state || "") || state === "down" || state === "stalled")) {
    return { kind: "offline", words: "host offline", why: a.adapter_reason || "Adapter offline; directed work will remain queued." };
  }
  if ((a.seen_h === null || a.seen_h === undefined) && !a.adapter_state) {
    return { kind: "unknown", words: "unknown", why: "No liveness record for this seat. Never seen by this board." };
  }
  const seen = seenWords(a);
  return { kind: "idle", words: `idle ${seen}`, why: `Reachable, nothing claimed. Last seen ${seen === "never" ? "never" : seen + " ago"}.` };
}

/** The one-line state under a recipient's name in Talk (§5.6). */
export function seatStateLine(a: AgentRow, nodes: ReadonlyArray<PlanNode> = []): string {
  const p = presenceOf(a, nodes);
  if (p.kind === "working") {
    const node = runningNodeOf(a.name, nodes, a.ticket || "");
    const elapsed = elapsedWords(node?.running?.elapsed_s);
    return node ? `running ${node.id}${elapsed ? ` for ${elapsed}` : ""}` : "working";
  }
  if (p.kind === "limited") return p.words;
  if (p.kind === "logged-out") return `logged out · ${p.why}`;
  if (p.kind === "offline") return "not running · host offline";
  if (p.kind === "unknown") return "not running · never seen";
  return `idle since ${seenWords(a)}`;
}

/** The seat the operator talks to by default (§4): the chosen lead, else the one running seat, else everyone. */
export function defaultRecipient(lead: string, agents: ReadonlyArray<AgentRow>, nodes: ReadonlyArray<PlanNode>): string {
  if (lead && agents.some((a) => a.name === lead)) return lead;
  const running = agents.filter((a) => presenceOf(a, nodes).kind === "working");
  return running.length === 1 ? running[0].name : "everyone";
}

/* ---------------------------------------------------------- counts, needs */

export interface Progress {
  accepted: number;
  total: number;
  /** done without an accept, shown as a suffix when non-zero. */
  unaccepted: number;
}

/** `1 of 3 accepted`: counts.done over the nodes minus the discarded ones; done-not-accepted does not count (§3). */
export function progressOf(plan: Pick<Plan, "counts" | "nodes">): Progress {
  const counts = plan.counts || {};
  const nodes = Array.isArray(plan.nodes) ? plan.nodes : [];
  const unaccepted = Number(counts.done_unverified) || 0;
  const done = Number(counts.done) || 0;
  return {
    accepted: Math.max(0, done - unaccepted),
    total: Math.max(0, nodes.length - (Number(counts.discarded) || 0)),
    unaccepted,
  };
}

export interface Strip {
  working: number;
  needs: number;
  blocked: number;
  done: number;
  unaccepted: number;
}

/** The four counts, read straight from plan.counts and needs-you.count; nothing added client-side (§1). */
export function stripOf(plan: Pick<Plan, "counts">, needsCount: number): Strip {
  const c = plan.counts || {};
  const unaccepted = Number(c.done_unverified) || 0;
  return {
    working: Number(c.working) || 0,
    needs: Math.max(0, needsCount || 0),
    blocked: Number(c.blocked) || 0,
    done: Math.max(0, (Number(c.done) || 0) - unaccepted),
    unaccepted,
  };
}

export type NeedsFilter = "all" | "mentions" | "accept" | "blocked";

/** Which Needs-you filter chip an item falls under (§5.10). */
export function needsKindOf(item: Pick<NeedsYouItem, "kind" | "why" | "text">): Exclude<NeedsFilter, "all"> {
  if (item.kind === "review") return "accept";
  if (item.kind === "seat") return "blocked";
  if (item.kind === "message" && /stuck/i.test(item.why)) return "blocked";
  return "mentions";
}

/** The ids the plan holds, so a `#T-001` in chat becomes a badge only when it is on this board (§5.9). */
export function nodeIndex(nodes: ReadonlyArray<PlanNode> | null | undefined): Map<string, PlanNode> {
  return byId(Array.isArray(nodes) ? nodes : []);
}
