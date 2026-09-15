import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { forgetLastGood, loadJudged, readBoardFiles } from "../src/server/board";
import { UNVERIFIED_DONE_LABEL, SUBMITTED_LABEL } from "../src/server/honesty";
import { appRouter } from "../src/server/router";

const FIXTURE = path.resolve(__dirname, "../fixtures/throwaway-board");

afterEach(() => forgetLastGood());

describe("same board files as tickets ui", () => {
  it("reads T-*.json, agents/*.json, messages.jsonl, objective.json", () => {
    const files = readBoardFiles(FIXTURE);
    expect(files.tickets.map((t) => t.id)).toEqual([
      "T-001", "T-002", "T-003", "T-004", "T-005", "T-006", "T-007",
    ]);
    expect(files.agents.map((a) => a.name)).toEqual(["alice", "bob"]);
    expect(files.messages.some((m) => m.kind === "task" && m.re === "T-001")).toBe(true);
    expect(files.objective.text).toBe("Ship the coherent Atman app");
  });
});

describe("T-986 honesty cases on the judged graph", () => {
  it("shows T-006 done-without-ACCEPT and does not count it as verified done", () => {
    const j = loadJudged(FIXTURE);
    const ids = j.nodes.map((n) => n.id);
    expect(ids).toContain("T-006");
    expect(ids).not.toContain("T-007");
    const six = j.nodes.find((n) => n.id === "T-006")!;
    expect(six.review.kind).toBe("unverified_done");
    expect(six.review.label).toBe(UNVERIFIED_DONE_LABEL);
    expect(j.counts.unverifiedDone).toBe(1);
    expect(j.counts.doneVerified).toBe(1);
  });

  it("T-005 submitted is not accepted", () => {
    const j = loadJudged(FIXTURE);
    const five = j.nodes.find((n) => n.id === "T-005")!;
    expect(five.review.kind).toBe("submitted");
    expect(five.review.accepted).toBe(false);
    expect(five.review.label).toBe(SUBMITTED_LABEL);
  });

  it("T-004 reserved is not claimed", () => {
    const j = loadJudged(FIXTURE);
    const four = j.nodes.find((n) => n.id === "T-004")!;
    expect(four.phase).toBe("reserved");
    expect(four.evidence).toMatch(/not claimed/);
  });

  it("alice binary-found is not connected; bob expired is not responding", () => {
    const j = loadJudged(FIXTURE);
    const alice = j.members.find((m) => m.reach.name === "alice")!;
    const bob = j.members.find((m) => m.reach.name === "bob")!;
    expect(alice.reach.phase).toBe("found");
    expect(alice.reach.connected).toBe(false);
    expect(bob.reach.phase).toBe("expired");
    expect(bob.reach.responding).toBe(false);
  });
});

describe("offline keeps the last snapshot", () => {
  it("removing the board dir after a live read is OFFLINE, not work lost", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "t1019-board-"));
    fs.cpSync(FIXTURE, tmp, { recursive: true });
    const live = loadJudged(tmp);
    expect(live.freshness.phase).toBe("live");
    expect(live.nodes.some((n) => n.id === "T-006")).toBe(true);
    fs.rmSync(tmp, { recursive: true, force: true });
    const offline = loadJudged(tmp);
    expect(offline.freshness.phase).toBe("offline");
    expect(offline.freshness.workLost).toBe(false);
    expect(offline.freshness.lastSnapshotKept).toBe(true);
    expect(offline.nodes.some((n) => n.id === "T-006")).toBe(true);
    expect(offline.objective.text).toBe("Ship the coherent Atman app");
  });
});

describe("tRPC router only returns judged views", () => {
  it("objective / work / team callers agree on the four rules", async () => {
    const caller = appRouter.createCaller({ boardDir: FIXTURE });
    const [objective, work, team] = await Promise.all([
      caller.objective(),
      caller.work(),
      caller.team(),
    ]);
    expect(objective.counts.unverifiedDone).toBe(1);
    expect(objective.counts.doneVerified).toBe(1);
    expect(work.nodes.find((n) => n.id === "T-006")?.review.label).toBe(UNVERIFIED_DONE_LABEL);
    expect(team.members.every((m) => m.reach.connected === false)).toBe(true);
  });
});
