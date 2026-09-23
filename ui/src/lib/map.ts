/**
 * The data mapping: API records in, rendered facts out.
 *
 * Every rule the spec calls an invariant lives here as a pure function, so it
 * is unit-testable against fixtures generated from the schemas:
 *
 * - **Done without an accept is never accepted.** `acceptState` derives the
 *   label for a finished node itself and refuses a `status_label` that claims
 *   an accept the record does not carry.
 * - **A blocker is a typed chip**, with dependency-not-accepted and
 *   dependency-still-open as two different things.
 * - **Usage always carries its age**, and a percentage that was never read
 *   stays absent instead of becoming 0.
 * - **Tokens are `unknown`, never 0.**
 * - **A harness with no record is `unknown`**, never guessed from anything.
 * - **Receipts are delivery facts**, labelled as receipts; an
 *   acknowledgement-shaped word is refused on screen rather than passed off as
 *   the agent having understood anything.
 */

import type {
  Blocker,
  BlockerKind,
  HarnessBadge,
  PlanNode,
  Post,
  Receipt,
  TicketRun,
  UsageView,
} from "../api/types";
import { NOT_RECORDED, UNKNOWN, ago, count, duration } from "./format";

export type Tone = "accepted" | "unverified" | "override" | "running" | "blocked" | "neutral";

/* ------------------------------------------------------------------ accepts */

export interface AcceptState {
  label: string;
  tone: Tone;
  accepted: boolean;
  /** Set when the record's own label had to be corrected, so the UI can say so. */
  corrected?: string;
}

const CLAIMS_ACCEPT = /\baccepted\b/i;

/**
 * What a node's or ticket's state may be called on screen.
 *
 * `accepted` is the only thing that makes the word "accepted" appear. Work
 * that is done with no structured accept reads *done, not accepted*; work
 * released by an override says so and does not read as accepted. If the
 * server's `status_label` claims an accept the `accepted` flag does not carry,
 * the honest label wins and `corrected` records the string that was refused.
 */
export function acceptState(rec: {
  status: string;
  status_label?: string;
  accepted: boolean;
  released?: boolean;
  unverified?: boolean;
}): AcceptState {
  const given = (rec.status_label || "").trim();
  const done = rec.status === "done";
  if (rec.accepted) return { label: "done, accepted", tone: "accepted", accepted: true };
  if (done && rec.released) {
    return { label: "done, released by override (not accepted)", tone: "override", accepted: false };
  }
  if (done) return { label: "done, not accepted", tone: "unverified", accepted: false };
  if (given && CLAIMS_ACCEPT.test(given)) {
    return { label: rec.status || UNKNOWN, tone: "neutral", accepted: false, corrected: given };
  }
  return { label: given || rec.status || UNKNOWN, tone: "neutral", accepted: false };
}

/* ----------------------------------------------------------------- blockers */

export interface BlockerChip {
  kind: BlockerKind;
  /** The short kind word on the chip. */
  label: string;
  /** The record's own sentence, shown beside the chip. */
  text: string;
  on: string;
  /** A copyable atm command, or "". */
  cmd: string;
  tone: Tone;
}

const BLOCKER_LABEL: Record<BlockerKind, string> = {
  dep_unaccepted: "dependency not accepted",
  dep_open: "dependency still open",
  seat_limited: "seat limited",
  seat_offline: "seat offline",
  auth: "auth",
  hold: "hold",
  capture: "capture",
  blocked: "blocked",
  unaccepted: "done, not accepted",
};

/** A typed chip per blocker. An unknown kind is shown as itself, never dropped. */
export function blockerChip(b: Blocker): BlockerChip {
  const known = (BLOCKER_LABEL as Record<string, string>)[b.kind];
  return {
    kind: b.kind,
    label: known || b.kind,
    text: b.text,
    on: b.on,
    cmd: b.cmd || "",
    tone: b.kind === "unaccepted" || b.kind === "dep_unaccepted" ? "unverified" : "blocked",
  };
}

export function blockerChips(node: { blockers?: Blocker[] }): BlockerChip[] {
  return (Array.isArray(node.blockers) ? node.blockers : []).map(blockerChip);
}

export interface StepBlockers {
  chips: BlockerChip[];
  /**
   * The command that would accept THIS step, when its only problem is that it
   * is finished without an accept.
   */
  acceptCmd: string;
}

/**
 * The blockers to show beside a step, given that its state chip already says
 * whether it is accepted.
 *
 * A finished-but-unaccepted step used to print the same phrase three times —
 * the state chip, a blocker chip and the blocker's own sentence — and the
 * override case printed *released by override (not accepted)* and then *done,
 * not accepted* twice, so the step looked like it disagreed with itself. The
 * `unaccepted` blocker for the step itself is therefore folded into the state
 * chip, and only the thing the state chip cannot say — the command — is kept.
 */
export function stepBlockers(node: { id: string; blockers?: Blocker[] }): StepBlockers {
  const chips: BlockerChip[] = [];
  let acceptCmd = "";
  for (const chip of blockerChips(node)) {
    if (chip.kind === "unaccepted" && (!chip.on || chip.on === node.id)) {
      acceptCmd = acceptCmd || chip.cmd;
      continue;
    }
    chips.push(chip);
  }
  return { chips, acceptCmd };
}

/**
 * The record's own sentence, when it adds something the chip's word does not.
 *
 * "hold: hold" is noise; "seat rev limited until 17:40" beside *seat limited*
 * is the detail that matters.
 */
export function blockerDetail(chip: BlockerChip): string {
  const text = (chip.text || "").trim();
  if (!text) return "";
  const label = chip.label.toLowerCase();
  const lower = text.toLowerCase();
  if (lower === label || lower === chip.kind.toLowerCase()) return "";
  return text;
}

/* -------------------------------------------------------------------- usage */

export interface UsageLine {
  provider: string;
  /** "62% left" or "share unknown" — never a number that was not read. */
  headline: string;
  /** Always present: "checked 3m ago" or "age unknown". */
  age: string;
  reset: string;
  text: string;
  /** true when checked_at was empty: the reading has no age at all. */
  ageUnknown: boolean;
}

/**
 * One usage reading with its age.
 *
 * The age is never optional, because a quota figure without one is a claim
 * about now made from a reading that may be hours old. With no `checked_at`
 * the contract requires `age: "age unknown"` and `remaining_pct: null`, and
 * this renders exactly that.
 */
export function usageLine(u: UsageView, now: Date = new Date()): UsageLine {
  const ageUnknown = !u.checked_at.trim();
  const pct = u.remaining_pct;
  const fresh = ageUnknown ? "" : ago(u.checked_at, now);
  return {
    provider: u.provider,
    headline:
      pct === null || pct === undefined ? `share ${UNKNOWN}` : `${Math.round(pct)}% left`,
    age: ageUnknown ? "age unknown" : `checked ${fresh || u.age}`,
    reset: u.reset || "",
    text: u.text || "",
    ageUnknown,
  };
}

/* ------------------------------------------------------------------ harness */

export interface HarnessChip {
  text: string;
  recorded: boolean;
  title: string;
}

/**
 * The harness badge on a post.
 *
 * `recorded: false` means the board holds no harness stamped at post time.
 * The badge then says so; it is never filled in from the seat's current
 * harness, and never defaulted to any provider.
 */
export function harnessChip(h: HarnessBadge | undefined | null): HarnessChip {
  if (!h || !h.value) {
    return { text: UNKNOWN, recorded: false, title: "no harness recorded for this post" };
  }
  return {
    text: h.value,
    recorded: !!h.recorded,
    title: h.note || (h.recorded ? "recorded at post time" : "not recorded at post time"),
  };
}

/* --------------------------------------------------------------------- runs */

export interface RunTokens {
  label: string;
  known: boolean;
  breakdown: string;
}

/**
 * A run's token figure.
 *
 * The contract says `tokens: null` means unknown and never 0, and pins
 * `tokens_label` to "unknown" in that case. This trusts neither side blindly:
 * a null count reads "unknown" whatever the label says.
 */
export function runTokens(run: Pick<TicketRun, "tokens" | "tokens_in" | "tokens_out" | "tokens_label">): RunTokens {
  if (run.tokens === null || run.tokens === undefined) {
    return { label: UNKNOWN, known: false, breakdown: "" };
  }
  const parts: string[] = [];
  if (run.tokens_in !== null && run.tokens_in !== undefined) parts.push(`${count(run.tokens_in)} in`);
  if (run.tokens_out !== null && run.tokens_out !== undefined) parts.push(`${count(run.tokens_out)} out`);
  return { label: `${count(run.tokens)} tokens`, known: true, breakdown: parts.join(" · ") };
}

/** "running 14m" / "done 6m" / "running, elapsed not recorded". */
export function runTiming(run: Pick<TicketRun, "state" | "elapsed_s">): string {
  const d = duration(run.elapsed_s);
  const state = run.state || UNKNOWN;
  return d ? `${state} ${d}` : `${state}, elapsed ${NOT_RECORDED}`;
}

export interface VerdictChip {
  label: string;
  /** The sha the verdict was recorded against, in full, or "". */
  sha: string;
}

/**
 * A verdict from a record whose shape the contract does not pin.
 *
 * `ticket.json` types `runs[].verdict` as a string, but the board snapshot's
 * `agent_map` rows — which `board.json` explicitly leaves informational — carry
 * `{kind, sha}` instead. Rendering that object directly is what React refuses,
 * and it took the whole app down once; so every verdict goes through here and
 * an unexpected shape becomes a plain, visible word rather than a crash.
 *
 * The sha is never shortened: it is kept whole for the caller to show or put in
 * a title, because a truncated sha cannot be compared with a review head.
 */
export function verdictChip(v: unknown): VerdictChip | null {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v === "string") return { label: v, sha: "" };
  if (typeof v === "object") {
    const rec = v as { kind?: unknown; sha?: unknown; verdict?: unknown };
    const kind = typeof rec.kind === "string" ? rec.kind : typeof rec.verdict === "string" ? rec.verdict : "";
    const sha = typeof rec.sha === "string" ? rec.sha : "";
    if (!kind && !sha) return { label: "verdict in an unknown shape", sha: "" };
    return { label: kind || "verdict recorded", sha };
  }
  return { label: String(v), sha: "" };
}

/* ----------------------------------------------------------------- receipts */

/** The words a receipt may not contain: a delivery fact is not an agent saying yes. */
const ACK_SHAPED = /acknowledg|\b(ack|ACK)\b|understood|\bon it\b/i;

export interface ReceiptLine {
  agent: string;
  words: string[];
  /** Words refused because they claim an acknowledgement rather than a delivery. */
  refused: string[];
}

/**
 * A delivery receipt, as delivery only.
 *
 * The contract forbids acknowledgement words in `receipt.words`, and the app
 * does not rely on that alone: anything ack-shaped is pulled out and shown as
 * refused, so a receipt can never be read as the agent having agreed to
 * anything. The UI labels the whole line "delivery receipt".
 */
export function receiptLine(r: Receipt): ReceiptLine {
  const words = r.words || [];
  return {
    agent: r.agent,
    words: words.filter((w) => !ACK_SHAPED.test(w)),
    refused: words.filter((w) => ACK_SHAPED.test(w)),
  };
}

export function receiptLines(post: Pick<Post, "receipts">): ReceiptLine[] {
  return (post.receipts || []).map(receiptLine);
}

/* ------------------------------------------------------------------ authors */

/**
 * `seat@project` for a post.
 *
 * The API already composes `author`; when a record predates that, the seat and
 * the project are joined here rather than showing a bare seat name that could
 * belong to any board.
 */
export function authorOf(post: Pick<Post, "author" | "from">, project: string): string {
  const given = (post.author || "").trim();
  if (given.includes("@")) return given;
  const seat = (post.from || "").trim() || UNKNOWN;
  return project ? `${seat}@${project}` : seat;
}

/* ------------------------------------------------------- text with ticket ids */

export type TextPart = { kind: "text"; value: string } | { kind: "ticket"; value: string } | { kind: "mention"; value: string };

const TICKET_RE = /\bT-\d{1,7}\b/g;
const MENTION_RE = /(?:^|[^A-Za-z0-9_@])@([A-Za-z0-9][A-Za-z0-9_.-]{0,63})/g;

/**
 * Split post text into plain runs, ticket links and mentions.
 *
 * A ticket id becomes a link **only** when that id is on this board's plan.
 * An id the board does not have stays plain text: a dead link would imply the
 * plan holds something it does not. `seat@project` is not a mention, so only
 * an `@name` that does not follow a word character is marked.
 */
export function textParts(text: string, knownTickets: ReadonlySet<string>): TextPart[] {
  const marks: Array<{ start: number; end: number; part: TextPart }> = [];
  for (const m of text.matchAll(TICKET_RE)) {
    if (m.index === undefined) continue;
    if (!knownTickets.has(m[0])) continue;
    marks.push({ start: m.index, end: m.index + m[0].length, part: { kind: "ticket", value: m[0] } });
  }
  for (const m of text.matchAll(MENTION_RE)) {
    if (m.index === undefined) continue;
    const at = text.indexOf("@" + m[1], m.index);
    if (at < 0) continue;
    marks.push({ start: at, end: at + m[1].length + 1, part: { kind: "mention", value: m[1] } });
  }
  marks.sort((a, b) => a.start - b.start);
  const parts: TextPart[] = [];
  let at = 0;
  for (const mark of marks) {
    if (mark.start < at) continue;
    if (mark.start > at) parts.push({ kind: "text", value: text.slice(at, mark.start) });
    parts.push(mark.part);
    at = mark.end;
  }
  if (at < text.length) parts.push({ kind: "text", value: text.slice(at) });
  return parts;
}

/* --------------------------------------------------------------------- plan */

/** The plan's running marker: "coder@alpha · 14m", or "" when nothing runs. */
export function runningLabel(node: Pick<PlanNode, "running">, project: string): string {
  const r = node.running;
  if (!r) return "";
  const seat = r.seat ? (project ? `${r.seat}@${project}` : r.seat) : UNKNOWN;
  const d = duration(r.elapsed_s);
  return d ? `${seat} · ${d}` : `${seat} · elapsed ${NOT_RECORDED}`;
}

/** The owner of a step as `seat@project`, or a visible "unassigned". */
export function ownerLabel(node: Pick<PlanNode, "owner" | "reserved_for">, project: string): string {
  const owner = (node.owner || "").trim();
  if (owner) return project ? `${owner}@${project}` : owner;
  const reserved = (node.reserved_for || "").trim();
  if (reserved) return `reserved for ${project ? `${reserved}@${project}` : reserved}`;
  return "unassigned";
}

/** The ids the plan holds, for linking ticket ids in prose. */
export function ticketIdsOf(nodes: Array<Pick<PlanNode, "id">>): Set<string> {
  return new Set(nodes.map((n) => n.id));
}

/**
 * One short workload line for the project switcher.
 *
 * The projects API counts by ticket *status* (`open` / `claimed` / …). The
 * plan body counts by *phase* (`working` / …). Saying "0 open" beside "1
 * working" for the same claimed ticket is a lie — prefer the plan's word when
 * the live work is claimed/review/blocked, and only say "N open" for
 * status-open tickets.
 */
export function projectWorkloadLine(counts: Record<string, number> | undefined): string | null {
  if (!counts || !Object.keys(counts).length) return null;
  const claimed = Number(counts.claimed) || 0;
  const review = Number(counts.review) || 0;
  const blocked = Number(counts.blocked) || 0;
  const open = Number(counts.open) || 0;
  const parts: string[] = [];
  if (claimed) parts.push(`${claimed} working`);
  if (review) parts.push(`${review} review`);
  if (blocked) parts.push(`${blocked} blocked`);
  if (open) parts.push(`${open} open`);
  if (parts.length) return parts.join(" · ");
  return "0 open";
}

/**
 * ACCEPTANCE PROOF on the drill-down.
 *
 * Prefer the sounding/capture sentence when present. For an accepted ticket,
 * fall back to the structured accept record (who + sha) — never "no proof
 * recorded" next to "Accepted by @seat". When an accept exists but does not
 * apply (no review_head), name that gap instead of "no proof recorded".
 */
export function acceptanceProofText(ticket: {
  accepted: boolean;
  acceptance?: { proof?: string };
  review?: { label?: string; verdicts?: Array<{ kind: string; by: string; sha: string; applies: boolean; superseded: boolean }> };
}): { text: string; missing: string } {
  const sounding = (ticket.acceptance?.proof || "").trim();
  if (sounding) return { text: sounding, missing: "" };
  if (!ticket.accepted) {
    const unbound = (ticket.review?.verdicts || []).find(
      (x) => x.kind === "accept" && !x.applies && !x.superseded,
    );
    if (unbound) {
      const sha = (unbound.sha || "").trim();
      return {
        text:
          `accept by @${unbound.by || "?"} on ${sha ? sha.slice(0, 7) : "?"} ` +
          "is not bound to a review head: run atm review, then accept at that sha",
        missing: "",
      };
    }
    return { text: "", missing: "no proof recorded" };
  }
  const label = (ticket.review?.label || "").trim();
  if (label) return { text: label, missing: "" };
  const v = (ticket.review?.verdicts || []).find(
    (x) => x.kind === "accept" && x.applies && !x.superseded,
  );
  if (v) {
    const sha = (v.sha || "").trim();
    return { text: `Accepted by @${v.by || "?"} on ${sha ? sha.slice(0, 7) : "unrecorded artifact"}`, missing: "" };
  }
  return { text: "", missing: "accepted, but accept who/sha not on the record" };
}

/* ------------------------------------------------------------ review header */

export interface ReviewHead {
  /** The full sha, never truncated — 40 characters when the board has one. */
  head: string;
  label: string;
  /** "" when there is nothing to say; else why the length is not 40. */
  note: string;
  /** true when the board holds no review head at all. */
  missing: boolean;
}

/**
 * The review head, in full.
 *
 * The drill-down shows all 40 characters because an accept is bound to exactly
 * this sha and a shortened one cannot be compared. A head that is not 40
 * characters is shown as it is, with its length, rather than padded or hidden.
 */
export function reviewHead(review: { head: string; head_len: number; label: string }): ReviewHead {
  const head = (review.head || "").trim();
  // `missing` rather than a note, so a caller cannot end up printing a phrase
  // like "no review head not recorded" by pairing this with its own wording.
  if (!head) return { head: "", label: review.label || "", note: "", missing: true };
  return {
    head,
    label: review.label || "",
    note: head.length === 40 ? "" : `${review.head_len || head.length} characters, not a 40-character sha`,
    missing: false,
  };
}
