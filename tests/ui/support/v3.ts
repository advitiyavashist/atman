/**
 * Boards in the shapes docs/design/app-v3.md §7 names, built from the
 * schemas: the same quickstart board the screenshots use (project `alpha` in
 * the fake, operator `ada`, seat `worker`, reviewer `astra`, tickets
 * T-001..T-003), one per state. Every field a test leans on is a field the
 * API writes today; the plan node's open fields (last_note, since_update_h,
 * running, dispatch) are the Work payload's own.
 */

import type { AgentRow, NeedsYouItem, PlanNode } from "../../../ui/src/api/types";
import type { FakeParts } from "./fake";
import { subFixture } from "./schema";

export const SHA = "3f2a1c0d9e8b7a6f5e4d3c2b1a0f9e8d7c6b5a49";
export const SHA2 = "9c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d";
export const OP = "ada";
export const REVIEW_NOTES = "schema.sql, docs/data-model.md; pytest tests/test_schema.py (6 passed); ids are ULIDs";

const NODE = "/properties/nodes/items";
const AGENT = "/properties/agents/items";
const ITEM = "/properties/items/items";

export const TITLE: Record<string, string> = {
  "T-001": "Sample: design the data model",
  "T-002": "Sample: build the API on top of the model",
  "T-003": "Sample: put a screen on the API",
};

export function stamp(minsAgo: number): string {
  return new Date(Date.now() - minsAgo * 60_000).toISOString();
}

export function node(id: string, over: Partial<PlanNode> & Record<string, unknown> = {}): PlanNode {
  const deps = id === "T-002" ? ["T-001"] : id === "T-003" ? ["T-002"] : [];
  return subFixture<PlanNode>("plan.json", NODE, {
    id,
    title: TITLE[id] || `${id} title`,
    status: "open",
    status_label: "TO DO",
    phase: "ready",
    deps,
    role: "backend",
    ...over,
  } as never);
}

/** A seat row as the board snapshot writes it: busy/idle/DOWN, reachable, adapter state. */
export function seat(name: string, over: Partial<AgentRow> & Record<string, unknown> = {}): AgentRow {
  return subFixture<AgentRow>("board.json", AGENT, {
    name,
    state: "idle",
    harness: "cursor",
    lifecycle: "persistent",
    reachable: true,
    adapter_state: "online",
    adapter_reason: "Adapter connected; no wake pending.",
    wake_mode: "continuous",
    limit: null,
    limit_until: "",
    auth_surface: { state: "ok", label: "signed in" },
    seen_h: 1 / 60,
    ...over,
  } as never);
}

export function need(over: Partial<NeedsYouItem>): NeedsYouItem {
  return subFixture<NeedsYouItem>("needs-you.json", ITEM, {
    kind: "message",
    why: "addressed to you, no reply from you since",
    id: "m1",
    at: stamp(20),
    from: "worker",
    author: "worker@alpha",
    re: "T-001",
    text: "@ada should ids be ULIDs or auto-increment ints?",
    state: "asked",
    label: "asked (unstructured)",
    ...over,
  } as never);
}

const OBJECTIVE = {
  text: "Ship a small stats API with a screen on top",
  exit_criterion: "the screen shows live stats from the API, accepted by a second seat",
  state: "active",
};

function waiting(id: string, on: string): PlanNode {
  return node(id, { phase: "waiting", wait: { kind: "deps", on: [on], text: `waits on ${on}`, cmd: `atm show ${on}` } });
}

function accepted(id: string): PlanNode {
  return node(id, {
    status: "done",
    status_label: "done, accepted",
    phase: "done",
    owner: "worker",
    accepted: true,
    released: true,
    unverified: false,
    done_at: stamp(120),
    review: { verified: true, label: `Accepted by @${OP} on ${SHA.slice(0, 7)}`, head: SHA },
    artifact: { commit: `worker/t-001@${SHA}`, branch: "worker/t-001", pr: "", sha: SHA },
    acceptance: { proof: "" },
  });
}

export function review(id: string, owner = "worker", head = SHA, notes = REVIEW_NOTES, minsAgo = 5): PlanNode {
  return node(id, {
    status: "review",
    status_label: "IN REVIEW",
    phase: "review",
    owner,
    since_update_h: minsAgo / 60,
    last_note: { by: owner, at: stamp(minsAgo), text: `REVIEW: ${notes}` },
    review: { verified: false, label: `Awaiting review of ${head.slice(0, 7)} · no verdict recorded`, head },
    artifact: { commit: `${owner}/t-001@${head}`, branch: `${owner}/t-001`, pr: "", sha: head },
  });
}

export function reviewItem(id: string, owner = "worker", notes = REVIEW_NOTES): NeedsYouItem {
  return need({ kind: "review", why: "waiting for your accept", id, at: stamp(5), from: owner, author: `${owner}@alpha`, re: id, text: notes, label: "review submitted" });
}

function plan(nodes: PlanNode[], counts: Record<string, number>, objective: Record<string, unknown> | null = OBJECTIVE): FakeParts["plan"] {
  return {
    available: true,
    objective: objective || {},
    counts,
    nodes,
    layers: nodes.map((n) => [n.id]),
    order: nodes.map((n) => n.id),
  };
}

function withApp(board: FakeParts["board"], operator = OP): FakeParts["board"] {
  return { ...board, app: { project: "alpha", operator, operator_note: "", lead: "", lead_note: "" } };
}

/** §7 states, as FakeParts. */
export const STATES: Record<string, FakeParts> = {
  empty: {
    plan: plan([], { working: 0, blocked: 0, done: 0 }, null),
    board: withApp({ agents: [seat("worker", { reachable: false, adapter_state: "offline", adapter_reason: "Adapter offline; directed work will remain queued.", seen_h: 72 })] }),
    needsYou: { items: [], count: 0 },
  },
  fresh: {
    plan: plan([node("T-001"), waiting("T-002", "T-001"), waiting("T-003", "T-002")], { working: 0, blocked: 0, done: 0, ready: 1, waiting: 2 }),
    board: withApp({ agents: [seat("worker")] }),
    needsYou: { items: [], count: 0 },
  },
  working: {
    plan: plan(
      [
        node("T-001", {
          status: "claimed",
          status_label: "IN PROGRESS",
          phase: "working",
          owner: "worker",
          running: { seat: "worker", elapsed_s: 12 * 60 },
          since_update_h: 2 / 60,
          last_note: { by: "worker", at: stamp(2), text: "update: drafting the schema: three tables, sites, samples, rollups" },
        }),
        waiting("T-002", "T-001"),
        waiting("T-003", "T-002"),
      ],
      { working: 1, blocked: 0, done: 0, waiting: 2 },
    ),
    board: withApp({ agents: [seat("worker", { state: "busy", ticket: "T-001" })] }),
    needsYou: { items: [], count: 0 },
  },
  "needs-you": {
    plan: plan([review("T-001"), waiting("T-002", "T-001"), waiting("T-003", "T-002")], { working: 0, blocked: 0, done: 0, review: 1, waiting: 2 }),
    board: withApp({ agents: [seat("worker", { seen_h: 5 / 60 }), seat("astra", { harness: "codex", seen_h: 2 })] }),
    needsYou: {
      items: [need({ id: "m1" }), reviewItem("T-001")],
      count: 2,
    },
    tickets: {
      "T-001": {
        id: "T-001",
        title: TITLE["T-001"],
        status: "review",
        status_label: "IN REVIEW",
        owner: "worker",
        owner_at_project: "worker@alpha",
        body: "A sample ticket created by `atm quickstart`.",
        review: { head: SHA, head_len: 40, label: "Awaiting review of 3f2a1c0 · no verdict recorded", verified: false, verdicts: [] },
        artifact: { commit: `worker/t-001@${SHA}`, branch: "worker/t-001", pr: "", sha: SHA },
        diff_cmd: `git diff main...${SHA}`,
        diff_note: "copy and run it in the repo; the app never runs git on a read",
      },
    },
  },
  blocked: {
    plan: plan(
      [
        accepted("T-001"),
        node("T-002", {
          status: "blocked",
          status_label: "BLOCKED",
          phase: "blocked",
          owner: "worker",
          since_update_h: 70 / 60,
          last_note: { by: "worker", at: stamp(70), text: "blocked: needs staging DB credentials: no STAGING_DB_URL on this machine and I cannot mint one" },
          wait: { kind: "blocked", on: [], text: "blocked: needs staging DB credentials", cmd: "atm reopen T-002" },
          blockers: [{ kind: "blocked", on: "T-002", text: "blocked: needs staging DB credentials", cmd: "atm reopen T-002" }],
        }),
        waiting("T-003", "T-002"),
      ],
      { working: 0, blocked: 1, done: 1, waiting: 1 },
    ),
    board: withApp({ agents: [seat("worker", { seen_h: 70 / 60 }), seat("astra", { harness: "codex", reachable: false, adapter_state: "offline", adapter_reason: "Adapter offline; directed work will remain queued.", seen_h: 3 })] }),
    needsYou: {
      items: [need({ id: "m9", why: "stuck for over an hour", at: stamp(70), re: "T-002", text: "stuck: needs staging DB credentials: no STAGING_DB_URL on this machine and I cannot mint one. #T-003 waits on this." })],
      count: 1,
    },
  },
  accepted: {
    plan: plan(
      [
        accepted("T-001"),
        node("T-002", {
          status: "claimed",
          status_label: "IN PROGRESS",
          phase: "working",
          owner: "worker",
          running: { seat: "worker", elapsed_s: 25 * 60 },
          since_update_h: 2 / 60,
          last_note: { by: "worker", at: stamp(2), text: "update: writing tests for the /stats endpoint; rollup job next" },
        }),
        waiting("T-003", "T-002"),
      ],
      { working: 1, blocked: 0, done: 1, waiting: 1 },
    ),
    board: withApp({ agents: [seat("worker", { state: "busy", ticket: "T-002" }), seat("astra", { harness: "codex", seen_h: 2 })] }),
    needsYou: { items: [], count: 0 },
  },
  offline: {
    plan: plan(
      [
        accepted("T-001"),
        node("T-002", {
          status: "claimed",
          status_label: "IN PROGRESS",
          phase: "working",
          owner: "worker",
          since_update_h: 30 / 60,
          last_note: { by: "worker", at: stamp(30), text: "update: /stats endpoint drafted; rollup job next" },
          blockers: [{ kind: "seat_limited", on: "worker", text: "seat worker limited until 17:40", cmd: "atm harness usage" }],
        }),
        waiting("T-003", "T-002"),
      ],
      { working: 1, blocked: 0, done: 1, waiting: 1 },
    ),
    board: withApp({
      agents: [
        seat("worker", { state: "LIMITED", ticket: "T-002", limit: { at: stamp(9), until: "17:40", note: "weekly cap" }, limit_until: "17:40" }),
        seat("astra", { harness: "codex", seen_h: null, adapter_state: "", reachable: false, adapter_reason: "" }),
      ],
    }),
    needsYou: {
      items: [need({ kind: "seat", why: "limited until 17:40", id: "seat:worker", at: "", from: "worker", author: "worker@alpha", re: "T-002", text: "worker is limited until 17:40. T-002 is waiting on it.", label: "seat needs attention" })],
      count: 1,
    },
  },
  "self-review": {
    plan: plan([review("T-001", OP), waiting("T-002", "T-001"), waiting("T-003", "T-002")], { working: 0, blocked: 0, done: 0, review: 1, waiting: 2 }),
    board: withApp({ agents: [seat("worker", { seen_h: 1 })] }),
    needsYou: { items: [need({ kind: "review", why: "you wrote this; another seat has to accept it", id: "T-001", from: OP, author: `${OP}@alpha`, re: "T-001", text: REVIEW_NOTES, label: "review submitted" })], count: 1 },
  },
};

/** The same board with no operator: the read-only app. */
export function readOnly(parts: FakeParts): FakeParts {
  return {
    ...parts,
    session: { ...parts.session, operator: "", operator_note: "no operator configured; start atm ui --operator <name>" },
    board: withApp(parts.board, ""),
  };
}
