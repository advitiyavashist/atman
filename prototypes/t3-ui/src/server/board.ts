/**
 * Reads the same files `tickets ui` / `board_snapshot` reads:
 *   {board}/T-*.json
 *   {board}/agents/*.json
 *   {board}/messages.jsonl
 *   {board}/objective.json
 *
 * No second store. Judgement happens in honesty.ts, not here.
 */

import fs from "node:fs";
import path from "node:path";
import {
  agentReach,
  boardLive,
  boardOffline,
  countWork,
  keepOnWorkGraph,
  reservedEvidence,
  reviewOf,
  workPhase,
  type AgentReach,
  type BoardFreshness,
  type ObjectiveView,
  type ReviewHonesty,
  type TeamMember,
  type WorkNode,
} from "./honesty";

export interface RawTicket {
  id: string;
  title?: string;
  body?: string;
  status?: string;
  role?: string;
  owner?: string;
  deps?: string[];
  lane?: string;
  hold?: boolean;
  reserved_for?: string;
  commit?: string;
  review_head?: string;
  review_events?: Array<{ kind?: string; by?: string; sha?: string }>;
  notes?: Array<{ by?: string; at?: string; text?: string }>;
  claimed_at?: string;
  updated?: string;
}

export interface RawAgent {
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
  ticket?: string;
  roles?: string[];
}

export interface RawMessage {
  id?: string;
  at?: string;
  from?: string;
  to?: string;
  re?: string;
  text?: string;
  kind?: string;
}

export interface BoardFiles {
  tickets: RawTicket[];
  agents: RawAgent[];
  messages: RawMessage[];
  objective: { text?: string; state?: string; exit?: string; exit_criterion?: string };
}

export interface WorkView {
  freshness: BoardFreshness;
  objective: ObjectiveView;
  nodes: WorkNode[];
  counts: ReturnType<typeof countWork>;
}

export interface TeamView {
  freshness: BoardFreshness;
  members: TeamMember[];
}

export interface ObjectiveScreen {
  freshness: BoardFreshness;
  objective: ObjectiveView;
  counts: ReturnType<typeof countWork>;
}

function readJson<T>(file: string, fallback: T): T {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8")) as T;
  } catch {
    return fallback;
  }
}

export function defaultBoardDir(): string {
  const env = process.env.ATMAN_T3_BOARD;
  if (env) return env;
  return path.resolve(process.cwd(), "fixtures/throwaway-board");
}

export function readBoardFiles(boardDir: string): BoardFiles {
  const tickets: RawTicket[] = [];
  if (fs.existsSync(boardDir)) {
    for (const name of fs.readdirSync(boardDir).sort()) {
      if (!/^T-.*\.json$/.test(name)) continue;
      const t = readJson<RawTicket | null>(path.join(boardDir, name), null);
      if (t && t.id) tickets.push(t);
    }
  }

  const agents: RawAgent[] = [];
  const agentsDir = path.join(boardDir, "agents");
  if (fs.existsSync(agentsDir)) {
    for (const name of fs.readdirSync(agentsDir).sort()) {
      if (!name.endsWith(".json")) continue;
      const a = readJson<RawAgent | null>(path.join(agentsDir, name), null);
      if (a) agents.push(a);
    }
  }

  const messages: RawMessage[] = [];
  const msgPath = path.join(boardDir, "messages.jsonl");
  if (fs.existsSync(msgPath)) {
    for (const line of fs.readFileSync(msgPath, "utf8").split("\n")) {
      if (!line.trim()) continue;
      try {
        messages.push(JSON.parse(line) as RawMessage);
      } catch {
        /* skip a corrupt line; do not invent a message */
      }
    }
  }

  const objective = readJson(path.join(boardDir, "objective.json"), {});
  return { tickets, agents, messages, objective };
}

function taskTicketIds(messages: RawMessage[]): Set<string> {
  const out = new Set<string>();
  for (const m of messages) {
    if ((m.kind || "") === "task" && (m.re || "").trim()) out.add(m.re!.trim());
  }
  return out;
}

function nodeOf(t: RawTicket, doneIds: Set<string>, tasked: Set<string>): WorkNode {
  const review: ReviewHonesty = reviewOf(t);
  const waiting = (t.deps || []).filter((d) => !doneIds.has(d));
  const reservedFor = (t.reserved_for || "").trim();
  const phase = workPhase(t, doneIds, tasked.has(t.id));
  let evidence = "";
  if (review.label) evidence = review.label;
  else if (phase === "reserved") evidence = reservedEvidence(reservedFor);
  else if (phase === "working") evidence = `Claimed by @${t.owner || "?"}`;
  else if (phase === "waiting") evidence = `Waiting on ${waiting.join(", ")}`;
  else if (phase === "hold") evidence = "HOLD";
  return {
    id: t.id,
    title: t.title || "",
    status: t.status || "open",
    phase,
    owner: t.owner || "",
    reservedFor,
    deps: t.deps || [],
    waiting,
    review,
    evidence,
  };
}

function nodesFrom(files: BoardFiles): { visible: WorkNode[]; all: WorkNode[] } {
  const doneIds = new Set(files.tickets.filter((t) => t.status === "done").map((t) => t.id));
  const tasked = taskTicketIds(files.messages);
  const all = files.tickets.map((t) => nodeOf(t, doneIds, tasked));
  return { all, visible: all.filter((n, i) => keepOnWorkGraph(files.tickets[i], n.review)) };
}

function objectiveFrom(files: BoardFiles, nodes: WorkNode[]): ObjectiveView {
  const raw = files.objective || {};
  const text = (raw.text || "").trim();
  const state = !text
    ? "unset"
    : raw.state === "achieved" || raw.state === "blocked" || raw.state === "replaced"
      ? raw.state
      : "active";
  const exitCriterion = (raw.exit_criterion || raw.exit || "").trim();
  const finishing = nodes
    .filter((n) => n.phase === "working" || n.phase === "review")
    .map((n) => ({ id: n.id, title: n.title, who: n.owner }));
  const blocked = nodes
    .filter((n) => n.phase === "hold" || n.phase === "blocked")
    .map((n) => ({ id: n.id, title: n.title, text: n.evidence || n.phase }));
  const nextNode = nodes.find((n) => n.phase === "reserved") || nodes.find((n) => n.phase === "ready") || null;
  return {
    text: text || "(no objective set)",
    state,
    exitCriterion,
    exitMissing: Boolean(text) && !exitCriterion,
    finishing,
    blocked,
    next: nextNode ? { id: nextNode.id, title: nextNode.title, evidence: nextNode.evidence } : null,
  };
}

export function judgeBoard(files: BoardFiles, asOf = new Date().toISOString()): {
  freshness: BoardFreshness;
  objective: ObjectiveView;
  nodes: WorkNode[];
  counts: ReturnType<typeof countWork>;
  members: TeamMember[];
} {
  const { visible, all } = nodesFrom(files);
  return {
    freshness: boardLive(asOf),
    objective: objectiveFrom(files, visible),
    nodes: visible,
    counts: countWork(all),
    members: files.agents.map((a) => ({
      reach: agentReach(a) as AgentReach,
      ticket: a.ticket || "",
      roles: a.roles || [],
    })),
  };
}

const lastGood = new Map<string, ReturnType<typeof judgeBoard>>();

export function loadJudged(boardDir: string): ReturnType<typeof judgeBoard> {
  try {
    if (!fs.existsSync(boardDir)) {
      const kept = lastGood.get(boardDir);
      if (kept) return { ...kept, freshness: boardOffline(kept.freshness.asOf, true) };
      return {
        freshness: boardOffline(null, false),
        objective: {
          text: "(board unreachable)",
          state: "unset",
          exitCriterion: "",
          exitMissing: false,
          finishing: [],
          blocked: [],
          next: null,
        },
        nodes: [],
        counts: countWork([]),
        members: [],
      };
    }
    const judged = judgeBoard(readBoardFiles(boardDir));
    lastGood.set(boardDir, judged);
    return judged;
  } catch {
    const kept = lastGood.get(boardDir);
    if (kept) return { ...kept, freshness: boardOffline(kept.freshness.asOf, true) };
    return {
      freshness: boardOffline(null, false),
      objective: {
        text: "(board unreachable)",
        state: "unset",
        exitCriterion: "",
        exitMissing: false,
        finishing: [],
        blocked: [],
        next: null,
      },
      nodes: [],
      counts: countWork([]),
      members: [],
    };
  }
}

export function workView(boardDir: string): WorkView {
  const j = loadJudged(boardDir);
  return { freshness: j.freshness, objective: j.objective, nodes: j.nodes, counts: j.counts };
}

export function teamView(boardDir: string): TeamView {
  const j = loadJudged(boardDir);
  return { freshness: j.freshness, members: j.members };
}

export function objectiveView(boardDir: string): ObjectiveScreen {
  const j = loadJudged(boardDir);
  return { freshness: j.freshness, objective: j.objective, counts: j.counts };
}

/** Test helper: drop the in-memory last-good snapshot. */
export function forgetLastGood(boardDir?: string): void {
  if (boardDir) lastGood.delete(boardDir);
  else lastGood.clear();
}
