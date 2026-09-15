import { describe, expect, it } from "vitest";
import {
  UNVERIFIED_DONE_LABEL,
  SUBMITTED_LABEL,
  agentReach,
  boardOffline,
  keepOnWorkGraph,
  reservedEvidence,
  reviewOf,
} from "../src/server/honesty";

describe("binary found is not connected", () => {
  it("login_required never claims connected or authenticated", () => {
    const r = agentReach({
      name: "alice",
      harness: "cursor",
      auth: "login_required",
      auth_detail: "cursor CLI found; not logged in",
      auth_login_cmd: "agent login",
      binary_found: true,
      adapter_online: false,
      reachable: false,
    });
    expect(r.phase).toBe("found");
    expect(r.connected).toBe(false);
    expect(r.authenticated).toBe(false);
    expect(r.responding).toBe(false);
    expect(r.label).toBe("Login required");
    expect(r.loginCmd).toBe("agent login");
  });

  it("expired token is not responding", () => {
    const r = agentReach({
      name: "bob",
      harness: "claude",
      auth: "expired",
      auth_detail: "claude binary found; token expired",
      auth_login_cmd: "claude login",
      binary_found: true,
    });
    expect(r.phase).toBe("expired");
    expect(r.connected).toBe(false);
    expect(r.responding).toBe(false);
    expect(r.label).toBe("Expired");
  });

  it("only a live authenticated adapter is connected", () => {
    const r = agentReach({
      name: "carol",
      harness: "cursor",
      auth: "ok",
      adapter_online: true,
    });
    expect(r.phase).toBe("responding");
    expect(r.connected).toBe(true);
    expect(r.authenticated).toBe(true);
    expect(r.responding).toBe(true);
  });
});

describe("submitted is not accepted", () => {
  it("REVIEW note + status=review is submitted, accepted=false", () => {
    const r = reviewOf({
      status: "review",
      commit: "alice@8cc7f39",
      review_events: [],
    });
    expect(r.kind).toBe("submitted");
    expect(r.accepted).toBe(false);
    expect(r.label).toBe(SUBMITTED_LABEL);
  });

  it("a chat completion note is not ACCEPT", () => {
    const r = reviewOf({
      status: "done",
      review_events: [],
    });
    expect(r.kind).toBe("unverified_done");
    expect(r.accepted).toBe(false);
    expect(r.label).toBe(UNVERIFIED_DONE_LABEL);
  });

  it("structured accept is the only accepted=true path", () => {
    const r = reviewOf({
      status: "done",
      review_head: "033cb738ecba2d552304ea2e7a2d46c30b9df476",
      review_events: [{ kind: "accept", by: "reviewer", sha: "033cb738ecba2d552304ea2e7a2d46c30b9df476" }],
    });
    expect(r.kind).toBe("accepted");
    expect(r.accepted).toBe(true);
    expect(r.label).toMatch(/Accepted by @reviewer on 033cb73/);
  });
});

describe("done-without-ACCEPT stays visible", () => {
  it("unverified done is kept on the work graph", () => {
    const r = reviewOf({ status: "done", review_events: [] });
    expect(keepOnWorkGraph({ status: "done" }, r)).toBe(true);
    expect(r.kind === "unverified_done" && r.visible).toBe(true);
  });

  it("verified done is omitted from the active graph", () => {
    const r = reviewOf({
      status: "done",
      review_events: [{ kind: "accept", by: "r", sha: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }],
    });
    expect(keepOnWorkGraph({ status: "done" }, r)).toBe(false);
  });
});

describe("disconnected is not work lost", () => {
  it("offline freshness never sets workLost", () => {
    const live = boardOffline("2026-09-15T00:00:00Z", true);
    expect(live.phase).toBe("offline");
    expect(live.workLost).toBe(false);
    expect(live.lastSnapshotKept).toBe(true);
    expect(live.recoveryCmd).toBe("tickets ui");
  });
});

describe("reserved is not claimed", () => {
  it("reserved copy refuses to say claimed", () => {
    expect(reservedEvidence("alice")).toBe("Reserved for @alice · no task posted · not claimed");
  });
});
