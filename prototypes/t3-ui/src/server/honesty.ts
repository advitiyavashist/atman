/**
 * T-1019: the judged view-model. This is the only place a screen is allowed
 * to learn what a ticket or agent *means*.
 *
 * Raw board fields stay in board.ts. Screens receive these types only.
 * A CLI binary on PATH is never `connected`. A worker note is never ACCEPT.
 * `status=done` without a structured accept event is visible, not counted
 * as verified progress, and never omitted from the work graph.
 */

export const UNVERIFIED_DONE_LABEL = "Marked done; verification not recorded";
export const SUBMITTED_LABEL = "Awaiting review · no verdict recorded";
export const OFFLINE_LABEL = "Board unreachable — retrying";
export const OFFLINE_RECOVERY_CMD = "tickets ui";

export type BoardPhase = "loading" | "live" | "offline";

export type BoardFreshness =
  | { phase: "loading"; asOf: null; workLost: false; lastSnapshotKept: false }
  | { phase: "live"; asOf: string; workLost: false; lastSnapshotKept: false }
  | {
      phase: "offline";
      asOf: string | null;
      workLost: false;
      lastSnapshotKept: boolean;
      label: typeof OFFLINE_LABEL;
      recoveryCmd: typeof OFFLINE_RECOVERY_CMD;
      recoveryIsRestart: true;
    };

export type AgentPhase = "missing" | "found" | "expired" | "authenticated" | "responding";

/**
 * Discriminated so `connected` is only true on `responding`.
 * TypeScript rejects `{ phase: "found", connected: true }`.
 */
export type AgentReach =
  | {
      phase: "missing";
      name: string;
      harness: string;
      connected: false;
      authenticated: false;
      responding: false;
      label: "Not installed";
      detail: string;
      loginCmd: string;
    }
  | {
      phase: "found";
      name: string;
      harness: string;
      connected: false;
      authenticated: false;
      responding: false;
      label: "Login required";
      detail: string;
      loginCmd: string;
    }
  | {
      phase: "expired";
      name: string;
      harness: string;
      connected: false;
      authenticated: false;
      responding: false;
      label: "Expired";
      detail: string;
      loginCmd: string;
    }
  | {
      phase: "authenticated";
      name: string;
      harness: string;
      connected: false;
      authenticated: true;
      responding: false;
      label: "Authenticated · not responding";
      detail: string;
      loginCmd: string;
    }
  | {
      phase: "responding";
      name: string;
      harness: string;
      connected: true;
      authenticated: true;
      responding: true;
      label: "Responding";
      detail: string;
      loginCmd: string;
    };

export type ReviewKind = "none" | "submitted" | "unverified_done" | "accepted" | "fix" | "rejected";

export type ReviewHonesty =
  | { kind: "none"; accepted: false; label: ""; artifact: string }
  | { kind: "submitted"; accepted: false; label: typeof SUBMITTED_LABEL; artifact: string }
  | {
      kind: "unverified_done";
      accepted: false;
      visible: true;
      label: typeof UNVERIFIED_DONE_LABEL;
      artifact: string;
    }
  | { kind: "accepted"; accepted: true; label: string; artifact: string; by: string }
  | { kind: "fix"; accepted: false; label: string; artifact: string; by: string }
  | { kind: "rejected"; accepted: false; label: string; artifact: string; by: string };

export type WorkPhase =
  | "working"
  | "review"
  | "blocked"
  | "posted"
  | "reserved"
  | "ready"
  | "waiting"
  | "capture"
  | "hold"
  | "done"
  | "discarded";

export interface WorkNode {
  id: string;
  title: string;
  status: string;
  phase: WorkPhase;
  owner: string;
  reservedFor: string;
  deps: string[];
  waiting: string[];
  review: ReviewHonesty;
  evidence: string;
}

export interface WorkCounts {
  working: number;
  review: number;
  reserved: number;
  waiting: number;
  hold: number;
  /** Isolated done without ACCEPT. Shown, never added to doneVerified. */
  unverifiedDone: number;
  /** Structured ACCEPT (or merge) only. */
  doneVerified: number;
}

export interface ObjectiveView {
  text: string;
  state: "unset" | "active" | "achieved" | "blocked" | "replaced";
  exitCriterion: string;
  exitMissing: boolean;
  finishing: { id: string; title: string; who: string }[];
  blocked: { id: string; title: string; text: string }[];
  next: { id: string; title: string; evidence: string } | null;
}

export interface TeamMember {
  reach: AgentReach;
  ticket: string;
  roles: string[];
}

export function boardLive(asOf: string): BoardFreshness {
  return { phase: "live", asOf, workLost: false, lastSnapshotKept: false };
}

export function boardOffline(asOf: string | null, lastSnapshotKept: boolean): BoardFreshness {
  return {
    phase: "offline",
    asOf,
    workLost: false,
    lastSnapshotKept,
    label: OFFLINE_LABEL,
    recoveryCmd: OFFLINE_RECOVERY_CMD,
    recoveryIsRestart: true,
  };
}

export function boardLoading(): BoardFreshness {
  return { phase: "loading", asOf: null, workLost: false, lastSnapshotKept: false };
}

function shortSha(raw: string): string {
  const s = raw.trim();
  if (!s) return "";
  const bare = s.includes("@") ? s.slice(s.lastIndexOf("@") + 1) : s;
  return bare.slice(0, 7);
}

export function artifactSha(ticket: {
  review_head?: string;
  commit?: string;
}): string {
  const head = (ticket.review_head || "").trim();
  if (/^[0-9a-f]{7,40}$/i.test(head)) return head.toLowerCase();
  const commit = (ticket.commit || "").trim();
  const bare = commit.includes("@") ? commit.slice(commit.lastIndexOf("@") + 1) : commit;
  return /^[0-9a-f]{7,40}$/i.test(bare) ? bare.toLowerCase() : "";
}

export function reviewOf(ticket: {
  status?: string;
  review_events?: Array<{ kind?: string; by?: string; sha?: string }>;
  review_head?: string;
  commit?: string;
}): ReviewHonesty {
  const artifact = artifactSha(ticket);
  const events = (ticket.review_events || []).filter(
    (e) => e && typeof e === "object" && (e.kind === "accept" || e.kind === "reject"),
  );
  const latest = events[events.length - 1];
  const st = ticket.status || "";

  if (latest?.kind === "accept") {
    const by = latest.by || "?";
    const sha = shortSha(latest.sha || artifact);
    return {
      kind: "accepted",
      accepted: true,
      by,
      artifact,
      label: `Accepted by @${by} on ${sha || "unrecorded artifact"}`,
    };
  }
  if (latest?.kind === "reject") {
    const by = latest.by || "?";
    const sha = shortSha(latest.sha || artifact);
    return {
      kind: "rejected",
      accepted: false,
      by,
      artifact,
      label: `Rejected by @${by} on ${sha || "unrecorded artifact"}`,
    };
  }
  if (st === "done") {
    return {
      kind: "unverified_done",
      accepted: false,
      visible: true,
      label: UNVERIFIED_DONE_LABEL,
      artifact,
    };
  }
  if (st === "review") {
    return { kind: "submitted", accepted: false, label: SUBMITTED_LABEL, artifact };
  }
  return { kind: "none", accepted: false, label: "", artifact };
}

export function agentReach(raw: {
  name?: string;
  owner?: string;
  harness?: string;
  auth?: string;
  auth_detail?: string;
  auth_login_cmd?: string;
  adapter_online?: boolean;
  adapter_native_online?: boolean;
  reachable?: boolean;
  binary_found?: boolean;
  binary?: string;
}): AgentReach {
  const name = raw.name || raw.owner || "?";
  const harness = raw.harness || "";
  const loginCmd = raw.auth_login_cmd || (harness ? `${harness} login` : "tickets connect");
  const detail = (raw.auth_detail || "").trim();
  const auth = (raw.auth || "").trim().toLowerCase();
  const live =
    raw.adapter_online === true ||
    raw.adapter_native_online === true ||
    raw.reachable === true;

  if (auth === "missing" || raw.binary_found === false) {
    return {
      phase: "missing",
      name,
      harness,
      connected: false,
      authenticated: false,
      responding: false,
      label: "Not installed",
      detail: detail || `${harness || "agent"} binary not found`,
      loginCmd,
    };
  }

  if (auth === "login_required" || auth === "found") {
    return {
      phase: "found",
      name,
      harness,
      connected: false,
      authenticated: false,
      responding: false,
      label: "Login required",
      detail: detail || `${harness} CLI found; not logged in`,
      loginCmd,
    };
  }

  if (auth === "expired") {
    return {
      phase: "expired",
      name,
      harness,
      connected: false,
      authenticated: false,
      responding: false,
      label: "Expired",
      detail: detail || `${harness} binary found; token expired`,
      loginCmd,
    };
  }

  if (live && (auth === "ok" || auth === "authenticated" || auth === "")) {
    return {
      phase: "responding",
      name,
      harness,
      connected: true,
      authenticated: true,
      responding: true,
      label: "Responding",
      detail: detail || "Adapter online",
      loginCmd,
    };
  }

  if (auth === "ok" || auth === "authenticated") {
    return {
      phase: "authenticated",
      name,
      harness,
      connected: false,
      authenticated: true,
      responding: false,
      label: "Authenticated · not responding",
      detail: detail || "Logged in; adapter not responding",
      loginCmd,
    };
  }

  // Binary present, no live adapter, no authoritative auth — still not connected.
  return {
    phase: "found",
    name,
    harness,
    connected: false,
    authenticated: false,
    responding: false,
    label: "Login required",
    detail: detail || (harness ? `${harness} CLI found; reach not confirmed` : "Reach not confirmed"),
    loginCmd,
  };
}

export function workPhase(ticket: {
  status?: string;
  lane?: string;
  hold?: boolean;
  body?: string;
  reserved_for?: string;
  owner?: string;
  deps?: string[];
}, doneIds: Set<string>, hasTask = false): WorkPhase {
  const st = ticket.status || "open";
  if (st === "done") return "done";
  if (st === "discarded") return "discarded";
  if (st === "blocked") return "blocked";
  if (st === "review") return "review";
  if (st === "claimed") return "working";
  if (ticket.hold || (ticket.body || "").trim().toUpperCase().startsWith("HOLD")) return "hold";
  if ((ticket.lane || "ready") === "capture") return "capture";
  const waiting = (ticket.deps || []).filter((d) => !doneIds.has(d));
  if (waiting.length) return "waiting";
  if (hasTask) return "posted";
  if ((ticket.reserved_for || "").trim()) return "reserved";
  return "ready";
}

export function reservedEvidence(reservedFor: string): string {
  const who = reservedFor.trim() || "?";
  return `Reserved for @${who} · no task posted · not claimed`;
}

export function countWork(nodes: WorkNode[]): WorkCounts {
  const counts: WorkCounts = {
    working: 0,
    review: 0,
    reserved: 0,
    waiting: 0,
    hold: 0,
    unverifiedDone: 0,
    doneVerified: 0,
  };
  for (const n of nodes) {
    if (n.review.kind === "accepted") counts.doneVerified += 1;
    else if (n.review.kind === "unverified_done") counts.unverifiedDone += 1;
    if (n.phase === "working") counts.working += 1;
    else if (n.phase === "review") counts.review += 1;
    else if (n.phase === "reserved") counts.reserved += 1;
    else if (n.phase === "waiting") counts.waiting += 1;
    else if (n.phase === "hold") counts.hold += 1;
  }
  return counts;
}

/** Isolated done-without-ACCEPT stays on the graph. Verified done may hide. */
export function keepOnWorkGraph(ticket: { status?: string }, review: ReviewHonesty): boolean {
  const st = ticket.status || "";
  if (st === "done") return review.kind === "unverified_done";
  return st === "open" || st === "claimed" || st === "blocked" || st === "review";
}
