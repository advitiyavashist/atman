/**
 * The one-screen view's pure model against the spec's tables: one sentence
 * per card (§5.3), six presence states (§5.8), the buckets (§3), the counts
 * (§1, §3) and the needs-you filters (§5.10). No browser, no invention.
 */

import { describe, expect, it } from "vitest";

import {
  bucketOf,
  defaultRecipient,
  doingNow,
  elapsedWords,
  needsKindOf,
  phaseWord,
  presenceOf,
  progressOf,
  relHours,
  seatStateLine,
  sentenceOf,
  stripOf,
  unboundAcceptSentence,
} from "../../ui/src/v3/model";
import { node, seat, need, SHA, OP, stamp } from "./support/v3";

const ctx = (nodes = [] as ReturnType<typeof node>[]) => ({ operator: OP, nodes, now: new Date() });

describe("time words", () => {
  it("reads minutes, hours and then a date", () => {
    expect(relHours(5 / 60)).toBe("5 min ago");
    expect(relHours(2)).toBe("2 h ago");
    expect(relHours(0.001)).toBe("just now");
    expect(relHours(30)).toMatch(/^on /);
    expect(relHours(null)).toBe("");
  });
  it("reads an elapsed run and never invents one", () => {
    expect(elapsedWords(12 * 60)).toBe("12 min");
    expect(elapsedWords(3600 + 300)).toBe("1 h 5 min");
    expect(elapsedWords(null)).toBe("");
  });
});

describe("one sentence per card (§5.3)", () => {
  it("working with a live run", () => {
    expect(sentenceOf(node("T-001", { phase: "working", status: "claimed", owner: "worker", running: { seat: "worker", elapsed_s: 720 } }), ctx())).toBe(
      "worker has it, running for 12 min.",
    );
  });
  it("working, no live run but claimed", () => {
    expect(sentenceOf(node("T-001", { phase: "working", status: "claimed", owner: "worker", since_update_h: 40 / 60 }), ctx())).toBe(
      "worker has it, last update 40 min ago.",
    );
  });
  it("working and silent", () => {
    expect(sentenceOf(node("T-001", { phase: "working", status: "claimed", owner: "worker", since_update_h: 2, stale: true }), ctx())).toBe(
      "worker has it but has been silent for 2 h.",
    );
  });
  it("posted, nobody picked up", () => {
    expect(sentenceOf(node("T-001", { phase: "posted", dispatch: { to: "worker", age_h: 8 / 60 } }), ctx())).toBe(
      "Posted to the board 8 min ago. Nobody has picked it up.",
    );
  });
  it("reserved", () => {
    expect(sentenceOf(node("T-001", { phase: "reserved", reserved_for: "worker" }), ctx())).toBe("Reserved for worker. Starts when worker wakes.");
  });
  it("in review, for the operator and for the author", () => {
    const n = node("T-001", { phase: "review", status: "review", owner: "worker", since_update_h: 5 / 60 });
    expect(sentenceOf(n, ctx())).toBe("worker finished it 5 min ago. Waiting for your accept.");
    expect(sentenceOf({ ...n, owner: OP }, ctx())).toBe("You finished it 5 min ago. Another seat has to accept it.");
  });
  it("in review with someone else's accept pending merge", () => {
    const n = node("T-001", { phase: "review", status: "review", owner: "worker", since_update_h: 1, review: { verified: true, label: "Accepted by @astra on 3f2a1c0" } });
    expect(sentenceOf(n, ctx())).toBe("Accepted by astra 1 h ago. Waiting for merge.");
  });
  it("rejected and back with the author", () => {
    const n = node("T-001", { phase: "working", status: "claimed", owner: "worker", review: { verified: false, label: "earlier REJECT by @ada on 3f2a1c0d9e (before resubmission)" } });
    expect(sentenceOf(n, ctx())).toBe("Rejected by ada on 3f2a1c0. Back with worker.");
  });
  it("ready, and waiting on a dep in each state", () => {
    expect(sentenceOf(node("T-001"), ctx())).toBe("Nobody has it. Ready to start.");
    const w = node("T-002", { phase: "waiting", wait: { kind: "deps", on: ["T-001"], text: "waits on T-001", cmd: "atm show T-001" } });
    expect(sentenceOf(w, ctx([node("T-001")]))).toBe("Nobody has it. Waits on T-001.");
    expect(sentenceOf(w, ctx([node("T-001", { phase: "review", status: "review" })]))).toBe("Nobody has it. Waits on T-001, which is in review.");
    expect(sentenceOf(w, ctx([node("T-001", { phase: "blocked", status: "blocked" })]))).toBe("Nobody has it. Waits on T-001, which is blocked.");
    expect(sentenceOf(w, ctx([node("T-001", { phase: "done", status: "done", status_label: "done, not accepted", accepted: false, unverified: true })]))).toBe(
      "Waits on T-001, which is done but not accepted.",
    );
  });
  it("hold and capture", () => {
    expect(sentenceOf(node("T-001", { phase: "hold", wait: { kind: "hold", on: [], text: "HOLD: tester week", cmd: "atm hold T-001 --clear" } }), ctx())).toBe("On hold: tester week.");
    expect(sentenceOf(node("T-001", { phase: "capture" }), ctx())).toBe("Waiting for capture: run atm sound.");
  });
  it("blocked, in the owner's words", () => {
    const n = node("T-002", { phase: "blocked", status: "blocked", owner: "worker", last_note: { by: "worker", at: stamp(40), text: "blocked: needs staging DB credentials" } });
    expect(sentenceOf(n, ctx())).toBe("Blocked by worker 40 min ago: “needs staging DB credentials”");
  });
  it("blocked by the seat: limited, offline, logged out", () => {
    const base = { phase: "working", status: "claimed", owner: "worker" } as const;
    expect(sentenceOf(node("T-002", { ...base, blockers: [{ kind: "seat_limited", on: "worker", text: "seat worker limited until 17:40", cmd: "atm harness usage" }] }), ctx())).toBe(
      "worker is limited until 17:40. Nothing runs on it until then.",
    );
    expect(sentenceOf(node("T-002", { ...base, blockers: [{ kind: "seat_offline", on: "worker", text: "seat worker offline", cmd: "atm spawn worker --persist" }] }), ctx())).toBe(
      "worker has no live session. It picks this up when started.",
    );
    expect(sentenceOf(node("T-002", { ...base, blockers: [{ kind: "auth", on: "worker", text: "seat worker logged out", cmd: "atm harness auth worker" }] }), ctx())).toBe(
      "worker is logged out. Run atm harness auth worker.",
    );
  });
  it("done: accepted, not accepted, override, and an accept that is not bound", () => {
    const done = { status: "done", phase: "done", owner: "worker", done_at: stamp(120) } as const;
    expect(sentenceOf(node("T-001", { ...done, status_label: "done, accepted", accepted: true, released: true, unverified: false, review: { verified: true, label: `Accepted by @${OP} on ${SHA.slice(0, 7)}` } }), ctx())).toBe(
      `Accepted by ${OP} 2 h ago on 3f2a1c0.`,
    );
    expect(sentenceOf(node("T-001", { ...done, status_label: "done, not accepted", accepted: false, unverified: true }), ctx())).toBe("Marked done by worker 2 h ago. Not accepted yet.");
    expect(sentenceOf(node("T-001", { ...done, status_label: "done, released by override (not accepted)", accepted: false, released: true, unverified: true }), ctx())).toBe(
      "Marked done by worker 2 h ago. Released by override, not accepted.",
    );
    // The T-1459 trap: a label that says Accepted while accepted is false.
    const unbound = node("T-001", { ...done, status_label: "done, not accepted", accepted: false, unverified: true, review: { verified: false, label: `Accepted by @${OP} on ${SHA.slice(0, 7)}` } });
    expect(sentenceOf(unbound, ctx())).toBe(unboundAcceptSentence("T-001", OP, SHA));
    expect(sentenceOf(unbound, ctx())).toContain("is not bound to a review head: run atm reopen T-001");
    expect(sentenceOf(unbound, ctx())).not.toMatch(/^Accepted/);
  });
});

describe("buckets and badges (§3, §5.9)", () => {
  it("puts each phase in its section", () => {
    expect(bucketOf(node("T-1", { phase: "review", status: "review" }))).toBe("needs");
    expect(bucketOf(node("T-1", { phase: "working", status: "claimed" }))).toBe("working");
    expect(bucketOf(node("T-1", { phase: "posted" }))).toBe("working");
    expect(bucketOf(node("T-1", { phase: "blocked", status: "blocked" }))).toBe("blocked");
    expect(bucketOf(node("T-1", { phase: "hold" }))).toBe("blocked");
    expect(bucketOf(node("T-1", { phase: "working", status: "claimed", blockers: [{ kind: "seat_limited", on: "w", text: "seat w limited until 17:40", cmd: "atm harness usage" }] }))).toBe("blocked");
    expect(bucketOf(node("T-1"))).toBe("next");
    expect(bucketOf(node("T-1", { phase: "waiting" }))).toBe("next");
    expect(bucketOf(node("T-1", { phase: "done", status: "done", status_label: "done, not accepted", unverified: true }))).toBe("done");
  });
  it("gives a badge its phase word, and done its accept state", () => {
    expect(phaseWord(node("T-1", { phase: "review", status: "review" }))).toBe("in review");
    expect(phaseWord(node("T-1", { phase: "done", status: "done", status_label: "done, accepted", accepted: true, released: true, unverified: false }))).toBe("accepted");
    expect(phaseWord(node("T-1", { phase: "done", status: "done", status_label: "done, not accepted", unverified: true }))).toBe("done, not accepted");
    expect(phaseWord(node("T-1", { phase: "waiting", wait: { kind: "deps", on: ["T-9"], text: "", cmd: "" } }))).toBe("waits on T-9");
  });
});

describe("presence (§5.8): six visuals, six states", () => {
  it("working, with the running ticket's last note as the words", () => {
    const n = node("T-002", { phase: "working", status: "claimed", owner: "worker", running: { seat: "worker", elapsed_s: 1500 }, last_note: { by: "worker", at: stamp(2), text: "update: writing tests for the /stats endpoint; rollup job next" } });
    const p = presenceOf(seat("worker", { state: "busy", ticket: "T-002" }), [n]);
    expect(p).toEqual({ kind: "working", words: "writing tests for the /stats endpoint for T-002", why: "" });
    expect(seatStateLine(seat("worker", { state: "busy", ticket: "T-002" }), [n])).toBe("running T-002 for 25 min");
  });
  it("working with no update since the claim says so rather than inventing one", () => {
    const n = node("T-002", { phase: "working", status: "claimed", owner: "worker", running: { seat: "worker", elapsed_s: 60 }, last_note: { by: OP, at: stamp(2), text: "assign: claimed for worker" } });
    expect(doingNow("worker", [n], "T-002")).toBe("running T-002, no update yet");
  });
  it("idle, with when it was last seen", () => {
    expect(presenceOf(seat("worker", { seen_h: 2 }))).toEqual({ kind: "idle", words: "idle 2 h", why: "Reachable, nothing claimed. Last seen 2 h ago." });
  });
  it("host offline carries the API's reason", () => {
    const p = presenceOf(seat("worker", { reachable: false, adapter_state: "offline", adapter_reason: "Adapter offline; directed work will remain queued.", seen_h: 3 }));
    expect(p).toEqual({ kind: "offline", words: "host offline", why: "Adapter offline; directed work will remain queued." });
  });
  it("limited and logged out are amber, with the fix", () => {
    expect(presenceOf(seat("worker", { limit: { until: "17:40" }, limit_until: "17:40" }))).toEqual({
      kind: "limited",
      words: "limited until 17:40",
      why: "Provider quota; resets 17:40. Nothing runs on it until then.",
    });
    expect(presenceOf(seat("worker", { auth_surface: { state: "login_required", label: "logged out", recovery: { cmd: "atm harness auth worker" } } }))).toEqual({
      kind: "logged-out",
      words: "logged out",
      why: "atm harness auth worker",
    });
  });
  it("unknown is a seat the board never saw, never a grey idle", () => {
    expect(presenceOf(seat("astra", { seen_h: null, adapter_state: "", reachable: false }))).toEqual({
      kind: "unknown",
      words: "unknown",
      why: "No liveness record for this seat. Never seen by this board.",
    });
  });
});

describe("who the operator talks to (§4)", () => {
  const running = node("T-001", { phase: "working", status: "claimed", owner: "worker", running: { seat: "worker", elapsed_s: 10 } });
  it("is the chosen lead when there is one", () => {
    expect(defaultRecipient("astra", [seat("worker", { state: "busy" }), seat("astra")], [running])).toBe("astra");
  });
  it("is the one running seat when exactly one runs", () => {
    expect(defaultRecipient("", [seat("worker", { state: "busy", ticket: "T-001" }), seat("astra")], [running])).toBe("worker");
  });
  it("is everyone otherwise, never an idle seat", () => {
    expect(defaultRecipient("", [seat("worker"), seat("astra")], [])).toBe("everyone");
    expect(defaultRecipient("", [seat("worker", { state: "busy" }), seat("astra", { state: "busy" })], [])).toBe("everyone");
    expect(defaultRecipient("gone", [seat("worker")], [])).toBe("everyone");
  });
});

describe("counts come from the board (§1, §3)", () => {
  it("reads the four counts and never adds client-side", () => {
    expect(stripOf({ counts: { working: 2, blocked: 1, done: 3, done_unverified: 1 } }, 4)).toEqual({ working: 2, needs: 4, blocked: 1, done: 2, unaccepted: 1 });
    expect(stripOf({ counts: {} }, 0)).toEqual({ working: 0, needs: 0, blocked: 0, done: 0, unaccepted: 0 });
  });
  it("counts progress as accepted over the plan minus discarded", () => {
    const nodes = [node("T-1"), node("T-2"), node("T-3"), node("T-4", { phase: "discarded" })];
    expect(progressOf({ counts: { done: 2, done_unverified: 1, discarded: 1 }, nodes })).toEqual({ accepted: 1, total: 3, unaccepted: 1 });
  });
  it("files each needs-you item under one filter chip", () => {
    expect(needsKindOf(need({}))).toBe("mentions");
    expect(needsKindOf(need({ why: "decision asked in prose" }))).toBe("mentions");
    expect(needsKindOf(need({ why: "stuck for over an hour" }))).toBe("blocked");
    expect(needsKindOf(need({ kind: "review", label: "review submitted" }))).toBe("accept");
    expect(needsKindOf(need({ kind: "seat", label: "seat needs attention" }))).toBe("blocked");
    expect(needsKindOf(need({ kind: "escalated" }))).toBe("mentions");
  });
});
