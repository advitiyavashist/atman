import { describe, expect, it } from "vitest";
import {
  assignmentStateLabel,
  agentStateLabel,
  DAY_ONE_STEPS,
  dependencyWaitingLabel,
  done24hLabel,
  formatDateTime,
  formatRelative,
  formatTime,
  formatWhen,
  hookEventKindLabel,
  parseBoardTime,
  streamStateCopy,
  ticketStatusLabel,
} from "../../ui/src/copy";

describe("copy rules that are contract, not styling", () => {
  it("never calls a queued reservation 'Working'", () => {
    expect(assignmentStateLabel.queued).toBe("Queued — waiting for agent");
    expect(assignmentStateLabel.queued).not.toMatch(/working/i);
  });

  it("keeps 'Working' only for an agent actually holding the ticket", () => {
    expect(agentStateLabel.working).toBe("Working");
  });

  it("renders a hook Stop event as Turn finished, never Ticket complete", () => {
    expect(hookEventKindLabel.stop).toBe("Turn finished");
    expect(hookEventKindLabel.stop).not.toMatch(/complete/i);
  });

  it("never implies work started on a dependency-blocked ticket", () => {
    expect(dependencyWaitingLabel("DEMO-13")).toBe("Waiting on DEMO-13");
  });

  it("labels an open dependency-blocked ticket distinctly from plain open", () => {
    expect(ticketStatusLabel({ state: "open", dependency_blocked: true })).toBe("Blocked on dependency");
    expect(ticketStatusLabel({ state: "open", dependency_blocked: false })).toBe("Ready");
  });

  it("labels the 24h completion stat Done(24h), distinct from ticket Done", () => {
    expect(done24hLabel).toBe("Done(24h)");
    expect(ticketStatusLabel({ state: "done", dependency_blocked: false })).toBe("Done");
  });

  it("keeps the day-one path as Objective, Team, Work", () => {
    expect(DAY_ONE_STEPS.map((s) => s.title)).toEqual(["Objective", "Team", "Work"]);
  });

  it("renders the stale-stream copy from the design doc verbatim in shape", () => {
    expect(streamStateCopy.stale("2026-09-06T14:32:00Z")).toMatch(/^Connection lost\. Showing updates from/);
  });
});

describe("board timestamps render in the viewer's local timezone", () => {
  const utc = "2026-09-06T14:32:00Z";
  const singapore = { timeZone: "Asia/Singapore" };
  const now = Date.parse("2026-09-06T16:32:00Z");

  it("converts stored UTC to Asia/Singapore wall time, never a bare Z string", () => {
    const absolute = formatDateTime(utc, singapore);
    const clock = formatTime(utc, singapore);
    // 14:32 UTC is 22:32 / 10:32 PM in Singapore (UTC+8).
    expect(absolute).toMatch(/10:32|22:32/);
    expect(clock).toMatch(/10:32|22:32/);
    expect(absolute).toMatch(/SGT|GMT\+8|UTC\+8/);
    expect(absolute).not.toMatch(/T14:32:00Z/);
    expect(clock).not.toMatch(/T14:32:00Z/);
    expect(absolute).not.toMatch(/14:32/);
  });

  it("treats a naive ISO stamp as UTC so it does not display as already-local", () => {
    expect(formatDateTime("2026-09-06T14:32:00", singapore)).toBe(formatDateTime(utc, singapore));
    expect(parseBoardTime("2026-09-06T14:32:00")?.toISOString()).toBe("2026-09-06T14:32:00.000Z");
  });

  it("pairs a relative age with the absolute local wall time", () => {
    const shown = formatWhen(utc, { ...singapore, now });
    expect(shown).toMatch(/^2h ago · /);
    expect(shown).toMatch(/10:32|22:32/);
    expect(shown).not.toMatch(/T14:32:00Z/);
    expect(formatRelative(utc, { now })).toBe("2h ago");
    expect(formatRelative("2026-09-06T16:40:00Z", { now })).toBe("in 8m");
  });

  it("returns unparseable input unchanged rather than inventing a clock", () => {
    expect(formatDateTime("not-a-time")).toBe("not-a-time");
    expect(formatWhen("")).toBe("");
  });
});
