/**
 * The fixture generator is itself under test, because every other test trusts
 * it. If these fail, the fixtures no longer stand in for the contract.
 */

import { describe, expect, it } from "vitest";

import { SCHEMAS } from "../../ui/src/api/schemas";
import type { Lead, Plan, Ticket, TicketRun, UsageView } from "../../ui/src/api/types";
import { defFixture, expectValid, fixture, subFixture } from "./support/schema";

const ROUTE_SCHEMAS = [
  "session.json",
  "projects.json",
  "board.json",
  "plan.json",
  "thread.json",
  "ticket.json",
  "lead.json",
  "needs-you.json",
  "lead-response.json",
  "error.json",
];

describe("fixtures come from the schemas", () => {
  it("loads every schema file the contract ships", () => {
    for (const file of ROUTE_SCHEMAS) expect(SCHEMAS[file], `${file} is not loaded`).toBeTruthy();
  });

  it("generates an instance of every route schema that validates", () => {
    for (const file of ROUTE_SCHEMAS) expectValid(file, fixture(file));
  });

  it("fails loudly when a test invents a field the schema does not have", () => {
    expect(() => fixture<Plan>("plan.json", { invented: true } as never)).toThrow(/plan\.json/);
  });

  it("fails loudly when an override breaks a contract rule", () => {
    // ticket.json pins status_label to "done, accepted" when accepted is true.
    expect(() =>
      fixture<Ticket>("ticket.json", { accepted: true, status: "done", status_label: "shipped it" }),
    ).toThrow(/does not satisfy/);
  });

  it("defaults a usage reading to no age and no percentage", () => {
    const u = defFixture<UsageView>("usage_view");
    expect(u.checked_at).toBe("");
    expect(u.age).toBe("age unknown");
    expect(u.remaining_pct).toBeNull();
  });

  it("refuses a usage reading that has a percentage but no age", () => {
    expect(() => defFixture<UsageView>("usage_view", { remaining_pct: 62 })).toThrow();
  });

  it("defaults a run's tokens to unknown rather than zero", () => {
    const run = subFixture<TicketRun>("ticket.json", "/properties/runs/items");
    expect(run.tokens).toBeNull();
    expect(run.tokens_label).toBe("unknown");
  });

  it("gives a board with a lead a status and an empty picker", () => {
    const lead = fixture<Lead>("lead.json", { lead: "planner", needs_lead: false });
    expect(lead.picker).toHaveLength(0);
    expect(lead.status).not.toBeNull();
    expect(lead.status?.usage.age).toBe("age unknown");
  });

  it("gives a board with no lead a picker and no status", () => {
    const lead = fixture<Lead>("lead.json", { needs_lead: true, lead: "" });
    expect(lead.status).toBeNull();
  });

  it("refuses a receipt word that claims an acknowledgement", () => {
    expect(() => defFixture("receipt", { agent: "planner", words: ["acknowledged"] })).toThrow();
    expect(() => defFixture("receipt", { agent: "planner", words: ["inbox read"] })).not.toThrow();
  });
});
