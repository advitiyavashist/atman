import { describe, expect, it } from "vitest";
import {
  assignmentStateLabel,
  agentStateLabel,
  dependencyWaitingLabel,
  formatDateTime,
  formatTime,
  hookEventKindLabel,
  parseUtcInstant,
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

  it("renders the stale-stream copy from the design doc verbatim in shape", () => {
    expect(streamStateCopy.stale("2026-09-06T14:32:00Z")).toMatch(/^Connection lost\. Showing updates from/);
  });
});

describe("timestamp formatting uses the host local timezone", () => {
  const utcIso = "2026-09-06T14:32:00Z";
  const localOpts: Intl.DateTimeFormatOptions = {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  };
  const timeOpts: Intl.DateTimeFormatOptions = { hour: "2-digit", minute: "2-digit" };

  it("converts stored UTC ISO through toLocaleString with no fixed zone", () => {
    const instant = new Date(utcIso);
    expect(formatDateTime(utcIso)).toBe(instant.toLocaleString(undefined, localOpts));
    expect(formatTime(utcIso)).toBe(instant.toLocaleTimeString(undefined, timeOpts));
    expect(formatDateTime(utcIso)).not.toMatch(/2026-09-06T14:32:00Z/);
  });

  it("treats a naive ISO stamp as UTC so the display layer can convert it", () => {
    expect(formatDateTime("2026-09-06T14:32:00")).toBe(formatDateTime(utcIso));
    expect(parseUtcInstant("2026-09-06T14:32:00")?.getTime()).toBe(new Date(utcIso).getTime());
  });

  it("labels the zone with the host abbreviation, not a silent Z", () => {
    const shown = formatDateTime(utcIso);
    const zone = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" })
      .formatToParts(new Date(utcIso))
      .find((part) => part.type === "timeZoneName")?.value;
    expect(zone).toBeTruthy();
    expect(shown).toContain(zone);
    expect(shown).not.toMatch(/Z$/);
    if (zone !== "UTC" && zone !== "GMT") {
      expect(shown).not.toMatch(/\bUTC\b/);
    }
  });

  it("returns the original string when the stamp is unparseable", () => {
    expect(formatDateTime("not-a-date")).toBe("not-a-date");
    expect(formatTime("")).toBe("");
    expect(parseUtcInstant("nope")).toBeNull();
  });
});
