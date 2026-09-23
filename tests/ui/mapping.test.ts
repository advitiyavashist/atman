/**
 * The data mapping, over fixtures generated from the schemas.
 *
 * These are the honesty rules as unit tests: a blocker's kind decides its
 * chip, done-without-an-accept is never accepted, a usage reading always
 * carries its age, an unrecorded harness is `unknown`, and a run with no token
 * record says `unknown` rather than 0.
 */

import { describe, expect, it } from "vitest";

import type { Blocker, HarnessBadge, PlanNode, Receipt, Ticket, TicketRun, UsageView } from "../../ui/src/api/types";
import { duration, durationLabel, localTime } from "../../ui/src/lib/format";
import {
  acceptState,
  acceptanceProofText,
  authorOf,
  blockerChip,
  blockerDetail,
  harnessChip,
  projectWorkloadLine,
  receiptLine,
  reviewHead,
  runTiming,
  runTokens,
  runningLabel,
  ownerLabel,
  stepBlockers,
  textParts,
  usageLine,
  verdictChip,
} from "../../ui/src/lib/map";
import { SCHEMAS } from "../../ui/src/api/schemas";
import { defFixture, fixture, subFixture } from "./support/schema";

const NODE = "/properties/nodes/items";
const RUN = "/properties/runs/items";

function node(over: Partial<PlanNode>): PlanNode {
  return subFixture<PlanNode>("plan.json", NODE, over as never);
}

function run(over: Partial<TicketRun>): TicketRun {
  return subFixture<TicketRun>("ticket.json", RUN, over as never);
}

describe("blocker chips", () => {
  it("tells a dependency that is not accepted from one that is still open", () => {
    const unaccepted = blockerChip(
      defFixture<Blocker>("blocker", { kind: "dep_unaccepted", on: "T-2", text: "dep T-2 done, not accepted", cmd: "atm accept T-2 --sha <review head>" }),
    );
    const open = blockerChip(
      defFixture<Blocker>("blocker", { kind: "dep_open", on: "T-3", text: "dep T-3 still claimed", cmd: "atm show T-3" }),
    );
    expect(unaccepted.label).toBe("dependency not accepted");
    expect(open.label).toBe("dependency still open");
    expect(unaccepted.label).not.toBe(open.label);
    expect(unaccepted.cmd).toContain("atm accept");
    expect(open.cmd).toContain("atm show");
  });

  it("labels a limited seat, an offline seat and an auth blocker apart", () => {
    const kinds = ["seat_limited", "seat_offline", "auth"] as const;
    const labels = kinds.map((kind) => blockerChip(defFixture<Blocker>("blocker", { kind, on: "rev", text: `${kind} text` })).label);
    expect(labels).toEqual(["seat limited", "seat offline", "auth"]);
    expect(new Set(labels).size).toBe(3);
  });

  it("keeps the record's own sentence beside the chip", () => {
    const chip = blockerChip(
      defFixture<Blocker>("blocker", { kind: "seat_limited", on: "rev", text: "seat rev limited until 17:40" }),
    );
    expect(chip.text).toBe("seat rev limited until 17:40");
  });

  it("passes the dependency sentence through verbatim, whatever the status word is", () => {
    // The API composes this sentence from the dep's own status word, and that
    // word has already changed once (`still in flight` -> `still claimed`).
    // The app must never match on the phrase or rebuild it: the chip's word
    // comes from the closed `kind` enum, the sentence comes from the record.
    for (const text of [
      "dep T-3 still claimed",
      "dep T-3 still in flight",
      "dep T-7 still review",
      "dep T-9 still some-word-this-app-has-never-seen",
      "dep T-42 is not on this board",
    ]) {
      const chip = blockerChip(defFixture<Blocker>("blocker", { kind: "dep_open", on: "T-3", text }));
      expect(chip.text).toBe(text);
      expect(chip.label).toBe("dependency still open");
      expect(blockerDetail(chip)).toBe(text);
    }
  });

  it("shows an unknown kind as itself rather than dropping it", () => {
    const chip = blockerChip({ kind: "decide_open" as Blocker["kind"], on: "D-7", text: "waiting on D-7", cmd: "" });
    expect(chip.label).toBe("decide_open");
  });

  it("has a plain-English word for every kind the contract declares", () => {
    // The closed list comes from common.json's own enum, so a kind added to
    // the contract without a word here fails this test.
    const kinds = (
      (SCHEMAS["common.json"] as { $defs: { blocker: { properties: { kind: { enum: string[] } } } } }).$defs.blocker
        .properties.kind.enum
    );
    expect(kinds.length).toBeGreaterThan(0);
    const labels = kinds.map((kind) => blockerChip({ kind: kind as Blocker["kind"], on: "T-1", text: "x", cmd: "" }).label);
    for (const [i, label] of labels.entries()) {
      expect(label, `${kinds[i]} has no word`).toBeTruthy();
      expect(label, `${kinds[i]} renders its raw key`).not.toContain("_");
    }
    expect(new Set(labels).size, "two kinds share one word").toBe(kinds.length);
  });
});

describe("accepted is only ever an accept", () => {
  it("says done, accepted only when the record carries an accept", () => {
    const n = node({ status: "done", accepted: true, unverified: false, status_label: "done, accepted" });
    const state = acceptState(n);
    expect(state.accepted).toBe(true);
    expect(state.label).toBe("done, accepted");
  });

  it("renders done without an accept as done, not accepted", () => {
    const n = node({ status: "done", accepted: false, released: false, unverified: true, status_label: "done, not accepted" });
    const state = acceptState(n);
    expect(state.accepted).toBe(false);
    expect(state.label).toBe("done, not accepted");
    expect(state.label).not.toContain("done, accepted");
  });

  it("says a release override is not an accept", () => {
    const n = node({
      status: "done",
      accepted: false,
      released: true,
      unverified: true,
      status_label: "done, released by override (not accepted)",
    });
    const state = acceptState(n);
    expect(state.label).toBe("done, released by override (not accepted)");
    expect(state.accepted).toBe(false);
    expect(state.tone).toBe("override");
  });

  it("refuses a status_label that claims an accept the record does not have", () => {
    // A server (or a future field) claiming this must not be able to put the
    // word on screen; the label is derived, and the claim is reported.
    const state = acceptState({ status: "review", accepted: false, status_label: "accepted by rev" });
    expect(state.label).toBe("review");
    expect(state.corrected).toBe("accepted by rev");
  });

  it("maps a ticket the same way as a plan node", () => {
    const t = fixture<Ticket>("ticket.json", { status: "done", accepted: false, status_label: "done, not accepted" });
    expect(acceptState(t).label).toBe("done, not accepted");
  });
});

describe("usage carries its age", () => {
  it("says age unknown and shows no percentage when nothing was read", () => {
    const u = defFixture<UsageView>("usage_view", { provider: "codex" });
    const line = usageLine(u);
    expect(line.ageUnknown).toBe(true);
    expect(line.age).toBe("age unknown");
    expect(line.headline).toBe("share unknown");
    expect(line.headline).not.toMatch(/\d/);
  });

  it("shows the reading with how old it is", () => {
    const now = new Date("2026-09-20T12:00:00Z");
    const u = defFixture<UsageView>("usage_view", {
      provider: "codex",
      remaining_pct: 62,
      checked_at: "2026-09-20T11:48:00Z",
      age: "12m",
      reset: "17:40",
    });
    const line = usageLine(u, now);
    expect(line.headline).toBe("62% left");
    expect(line.age).toBe("checked 12m ago");
    expect(line.reset).toBe("17:40");
  });

  it("never turns an unread quota into zero", () => {
    const line = usageLine(defFixture<UsageView>("usage_view", { provider: "claude" }));
    expect(line.headline).not.toContain("0%");
  });
});

describe("harness is never guessed", () => {
  it("shows unknown for a post with no recorded harness", () => {
    const chip = harnessChip(defFixture<HarnessBadge>("harness_badge", { value: "unknown", recorded: false, note: "not recorded at post time" }));
    expect(chip.text).toBe("unknown");
    expect(chip.recorded).toBe(false);
    expect(chip.title).toBe("not recorded at post time");
  });

  it("shows unknown, not a provider, when the badge is missing entirely", () => {
    expect(harnessChip(undefined).text).toBe("unknown");
    expect(harnessChip(null).text).toBe("unknown");
    expect(harnessChip(undefined).text).not.toBe("claude");
  });

  it("marks a harness that was stamped at post time as recorded", () => {
    const chip = harnessChip(defFixture<HarnessBadge>("harness_badge", { value: "codex", recorded: true, note: "recorded at post time" }));
    expect(chip.text).toBe("codex");
    expect(chip.recorded).toBe(true);
  });
});

describe("tokens and elapsed time", () => {
  it("says unknown for a run with no token record, never 0", () => {
    const r = run({ state: "running", elapsed_s: 840 });
    const tok = runTokens(r);
    expect(r.tokens).toBeNull();
    expect(tok.label).toBe("unknown");
    expect(tok.known).toBe(false);
    expect(tok.label).not.toContain("0");
  });

  it("reads a recorded count with its split", () => {
    const tok = runTokens(run({ tokens: 41200, tokens_in: 30000, tokens_out: 11200, tokens_label: "41,200" }));
    expect(tok.label).toBe("41,200 tokens");
    expect(tok.breakdown).toBe("30,000 in · 11,200 out");
  });

  it("says unknown even if the label disagrees with a null count", () => {
    // The contract pins tokens_label to "unknown" when tokens is null; the app
    // does not depend on that being true to stay honest.
    expect(runTokens({ tokens: null, tokens_in: null, tokens_out: null, tokens_label: "41,200" }).label).toBe("unknown");
  });

  it("says elapsed is not recorded rather than 0s", () => {
    expect(runTiming({ state: "running", elapsed_s: null })).toBe("running, elapsed not recorded");
    expect(runTiming({ state: "done", elapsed_s: 360 })).toBe("done 6m");
    expect(durationLabel(null)).toBe("not recorded");
    expect(duration(0)).toBe("0s");
  });
});

describe("receipts are deliveries", () => {
  it("passes delivery words through", () => {
    const line = receiptLine(defFixture<Receipt>("receipt", { agent: "planner", words: ["posted", "inbox read", "wake confirmed"] }));
    expect(line.words).toEqual(["posted", "inbox read", "wake confirmed"]);
    expect(line.refused).toEqual([]);
  });

  it("refuses an acknowledgement-shaped word even if one reaches the app", () => {
    const line = receiptLine({ agent: "planner", words: ["posted", "acknowledged", "ACK", "understood", "on it"] });
    expect(line.words).toEqual(["posted"]);
    expect(line.refused).toEqual(["acknowledged", "ACK", "understood", "on it"]);
  });
});

describe("identity, links and heads", () => {
  it("shows a post author as seat@project", () => {
    expect(authorOf({ author: "planner@alpha", from: "planner" }, "alpha")).toBe("planner@alpha");
    expect(authorOf({ author: "", from: "planner" }, "alpha")).toBe("planner@alpha");
  });

  it("links a ticket id only when the plan holds it", () => {
    const parts = textParts("T-4 waits on T-99", new Set(["T-4"]));
    expect(parts.filter((p) => p.kind === "ticket").map((p) => p.value)).toEqual(["T-4"]);
    expect(parts.some((p) => p.kind === "text" && p.value.includes("T-99"))).toBe(true);
  });

  it("does not read seat@project as a mention", () => {
    const parts = textParts("ask coder@demo about @rev", new Set());
    expect(parts.filter((p) => p.kind === "mention").map((p) => p.value)).toEqual(["rev"]);
  });

  it("keeps the whole 40-character review head", () => {
    const head = "9c1e5a0b7d3f2e1a4b6c8d0e2f4a6b8c0d1e3f50";
    const out = reviewHead({ head, head_len: 40, label: "rejected at this head" });
    expect(out.head).toBe(head);
    expect(out.head).toHaveLength(40);
    expect(out.note).toBe("");
  });

  it("says when a head is not a 40-character sha instead of padding it", () => {
    const short = reviewHead({ head: "9c1e5a0", head_len: 7, label: "" });
    expect(short.note).toContain("not a 40-character sha");
    expect(short.missing).toBe(false);
  });

  it("reports a missing head as a flag, not as words a caller can double up", () => {
    // The screen used to print "no review head not recorded", because it
    // wrapped this note in its own "no ..." phrasing.
    const none = reviewHead({ head: "", head_len: 0, label: "" });
    expect(none.missing).toBe(true);
    expect(none.note).toBe("");
  });

  it("names the running seat and the owner as seat@project", () => {
    expect(runningLabel(node({ running: { seat: "coder", elapsed_s: 840 } }), "alpha")).toBe("coder@alpha · 14m");
    expect(runningLabel(node({ running: { seat: "coder", elapsed_s: null } }), "alpha")).toContain("elapsed not recorded");
    expect(runningLabel(node({ running: null }), "alpha")).toBe("");
    expect(ownerLabel(node({ owner: "coder" }), "alpha")).toBe("coder@alpha");
    expect(ownerLabel(node({ owner: "", reserved_for: "rev" }), "alpha")).toBe("reserved for rev@alpha");
    expect(ownerLabel(node({ owner: "", reserved_for: "" }), "alpha")).toBe("unassigned");
  });

  it("says a post with no timestamp has none", () => {
    expect(localTime("")).toBe("time not recorded");
    // Local-day words, checked without assuming the runner's zone: the same
    // instant is always "today", and 24 hours earlier is always "yesterday".
    const now = new Date("2026-09-20T17:00:00Z");
    expect(localTime(now.toISOString(), now)).toMatch(/^Today at /);
    expect(localTime(new Date(now.getTime() - 24 * 3600 * 1000).toISOString(), now)).toMatch(/^Yesterday at /);
  });
});

describe("verdicts from records the contract does not pin", () => {
  it("reads a verdict word", () => {
    expect(verdictChip("reject")).toEqual({ label: "reject", sha: "" });
  });

  it("reads the {kind, sha} shape the board snapshot actually records", () => {
    // This is the real shape of agent_map.groups[].rows[].verdict on a live
    // board. Rendering the object itself is what React refuses, and it blanked
    // the whole app once; the sha is kept whole, never shortened.
    const sha = "9c1e5a0b7d3f2e1a4b6c8d0e2f4a6b8c0d1e3f50";
    expect(verdictChip({ kind: "reject", sha })).toEqual({ label: "reject", sha });
  });

  it("says so rather than crashing on a shape it has never seen", () => {
    expect(verdictChip({ something: 1 })).toEqual({ label: "verdict in an unknown shape", sha: "" });
    expect(verdictChip({ sha: "abc" })).toEqual({ label: "verdict recorded", sha: "abc" });
  });

  it("is nothing at all when there is no verdict", () => {
    expect(verdictChip("")).toBeNull();
    expect(verdictChip(null)).toBeNull();
    expect(verdictChip(undefined)).toBeNull();
  });
});

describe("a step says its state once", () => {
  it("folds the step's own unaccepted blocker into the state chip, keeping the command", () => {
    // Before this, a finished-but-unaccepted step printed "done, not accepted"
    // three times: the state chip, the blocker chip, and the blocker's text.
    const n = node({
      id: "T-2",
      status: "done",
      accepted: false,
      unverified: true,
      status_label: "done, not accepted",
      blockers: [{ kind: "unaccepted", on: "T-2", text: "done, not accepted", cmd: "atm accept T-2 --sha <review head>" }],
    });
    const { chips, acceptCmd } = stepBlockers(n);
    expect(chips).toHaveLength(0);
    expect(acceptCmd).toBe("atm accept T-2 --sha <review head>");
    expect(acceptState(n).label).toBe("done, not accepted");
  });

  it("does not fold an unaccepted blocker that is about another ticket", () => {
    const n = node({
      id: "T-5",
      blockers: [{ kind: "unaccepted", on: "T-2", text: "done, not accepted", cmd: "atm accept T-2" }],
    });
    expect(stepBlockers(n).chips).toHaveLength(1);
    expect(stepBlockers(n).acceptCmd).toBe("");
  });

  it("keeps every other blocker, in order", () => {
    const n = node({
      id: "T-4",
      blockers: [
        { kind: "dep_open", on: "T-3", text: "dep T-3 still claimed", cmd: "atm show T-3" },
        { kind: "seat_limited", on: "rev", text: "seat rev limited until 17:40", cmd: "atm harness usage" },
        { kind: "unaccepted", on: "T-4", text: "done, not accepted", cmd: "atm accept T-4" },
      ],
    });
    const { chips, acceptCmd } = stepBlockers(n);
    expect(chips.map((c) => c.kind)).toEqual(["dep_open", "seat_limited"]);
    expect(acceptCmd).toBe("atm accept T-4");
  });

  it("drops a blocker sentence that only repeats its own chip", () => {
    expect(blockerDetail(blockerChip({ kind: "hold", on: "T-1", text: "hold", cmd: "" }))).toBe("");
    expect(blockerDetail(blockerChip({ kind: "capture", on: "T-1", text: "capture", cmd: "" }))).toBe("");
    expect(
      blockerDetail(blockerChip({ kind: "seat_limited", on: "rev", text: "seat rev limited until 17:40", cmd: "" })),
    ).toBe("seat rev limited until 17:40");
  });

  it("survives a node whose blockers are not a list", () => {
    expect(stepBlockers({ id: "T-1", blockers: "nope" as never }).chips).toEqual([]);
  });
});

describe("project workload line (T-1459)", () => {
  it("says working for claimed tickets, not 0 open beside 1 working", () => {
    expect(projectWorkloadLine({ open: 0, claimed: 1, review: 0, blocked: 0, done: 1 })).toBe("1 working");
  });

  it("lists each live status with the plan's words", () => {
    expect(projectWorkloadLine({ open: 2, claimed: 1, review: 1, blocked: 3, done: 0 })).toBe(
      "1 working · 1 review · 3 blocked · 2 open",
    );
  });

  it("treats an empty counts object as unavailable", () => {
    expect(projectWorkloadLine({})).toBeNull();
    expect(projectWorkloadLine(undefined)).toBeNull();
  });
});

describe("acceptance proof (T-1459)", () => {
  it("prefers the sounding proof when present", () => {
    expect(
      acceptanceProofText({
        accepted: true,
        acceptance: { proof: "row count matches" },
        review: { label: "Accepted by @bob on abc1234" },
      }).text,
    ).toBe("row count matches");
  });

  it("uses the accept record when sounding proof is empty on an accepted ticket", () => {
    const out = acceptanceProofText({
      accepted: true,
      acceptance: { proof: "" },
      review: {
        label: "Accepted by @bob on 6cf5700",
        verdicts: [
          {
            kind: "accept",
            by: "bob",
            sha: "6cf57003447931cf822f50ee8aeca2389700507b",
            applies: true,
            superseded: false,
          },
        ],
      },
    });
    expect(out.text).toBe("Accepted by @bob on 6cf5700");
    expect(out.missing).toBe("");
  });

  it("does not say no proof recorded next to an accepted ticket", () => {
    const out = acceptanceProofText({
      accepted: true,
      acceptance: { proof: "" },
      review: { label: "", verdicts: [] },
    });
    expect(out.missing).toBe("accepted, but accept who/sha not on the record");
    expect(out.missing).not.toBe("no proof recorded");
  });
});
