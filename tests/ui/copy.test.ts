import { describe, expect, it } from "vitest";
import {
  assignmentStateLabel,
  agentStateLabel,
  dependencyWaitingLabel,
  hookEventKindLabel,
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
