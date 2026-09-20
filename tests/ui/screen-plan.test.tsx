/**
 * The execution plan, mounted.
 *
 * The plan is the centrepiece, so the assertions are about what it must never
 * get wrong: work that is done but not accepted, and *why* a step cannot move.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App } from "../../ui/src/App";
import type { Plan, PlanNode } from "../../ui/src/api/types";
import { fakeApi } from "./support/fake";
import { subFixture } from "./support/schema";

const NODE = "/properties/nodes/items";

function node(over: Partial<PlanNode>): PlanNode {
  return subFixture<PlanNode>("plan.json", NODE, over as never);
}

const ACCEPTED = node({
  id: "T-1",
  title: "Accepted base",
  status: "done",
  status_label: "done, accepted",
  phase: "done",
  owner: "coder",
  accepted: true,
  released: true,
  unverified: false,
  review: { verified: true, label: "accepted" },
});

const DONE_NOT_ACCEPTED = node({
  id: "T-2",
  title: "Done but never accepted",
  status: "done",
  status_label: "done, not accepted",
  phase: "done",
  owner: "scout",
  accepted: false,
  released: false,
  unverified: true,
  blockers: [{ kind: "unaccepted", on: "T-2", text: "done, not accepted", cmd: "atm accept T-2 --sha <review head>" }],
});

const WAITS_ON_UNACCEPTED = node({
  id: "T-5",
  title: "Waits on an unaccepted dep",
  status: "open",
  status_label: "open",
  phase: "waiting",
  deps: ["T-2"],
  blockers: [{ kind: "dep_unaccepted", on: "T-2", text: "dep T-2 done, not accepted", cmd: "atm accept T-2 --sha <review head>" }],
});

const WAITS_ON_OPEN = node({
  id: "T-4",
  title: "Waits on an open dep",
  status: "open",
  status_label: "open",
  phase: "waiting",
  deps: ["T-3"],
  reserved_for: "rev",
  blockers: [
    { kind: "dep_open", on: "T-3", text: "dep T-3 still in flight", cmd: "atm show T-3" },
    { kind: "seat_limited", on: "rev", text: "seat rev limited until 17:40", cmd: "atm harness usage" },
  ],
});

const RUNNING = node({
  id: "T-3",
  title: "In flight",
  status: "claimed",
  status_label: "in flight",
  phase: "working",
  owner: "coder",
  running: { seat: "coder", elapsed_s: 840 },
});

const OFFLINE = node({
  id: "T-6",
  title: "Posted to an offline seat",
  status: "open",
  status_label: "open",
  phase: "posted",
  reserved_for: "dave",
  blockers: [
    { kind: "seat_offline", on: "dave", text: "seat dave offline (queued-offline)", cmd: "atm spawn dave --persist" },
    { kind: "auth", on: "dave", text: "seat dave logged out", cmd: "atm auth reconnect dave" },
  ],
});

const PLAN: Partial<Plan> = {
  available: true,
  objective: { text: "Cut an honest developer preview", exit_criterion: "a fresh clone follows the README", state: "active" },
  counts: { open: 4, done: 2 },
  nodes: [ACCEPTED, DONE_NOT_ACCEPTED, RUNNING, WAITS_ON_OPEN, WAITS_ON_UNACCEPTED, OFFLINE],
  layers: [["T-1", "T-2"], ["T-3", "T-5"], ["T-4", "T-6"]],
  order: ["T-1", "T-2", "T-3", "T-5", "T-4", "T-6"],
};

function step(id: string): HTMLElement {
  const el = screen.getAllByTestId("plan-step").find((s) => s.getAttribute("data-ticket") === id);
  if (!el) throw new Error(`no step ${id} on screen`);
  return el;
}

describe("the execution plan", () => {
  it("shows the objective and its exit criterion", async () => {
    const { api } = fakeApi({ plan: PLAN });
    render(<App api={api} />);
    const objective = await screen.findByTestId("objective");
    expect(objective).toHaveTextContent("Cut an honest developer preview");
    expect(objective).toHaveTextContent("a fresh clone follows the README");
  });

  it("shows every step with its owner and state", async () => {
    const { api } = fakeApi({ plan: PLAN });
    render(<App api={api} />);
    await waitFor(() => expect(screen.getAllByTestId("plan-step")).toHaveLength(6));
    expect(step("T-3")).toHaveTextContent("coder@alpha");
    expect(step("T-3")).toHaveTextContent("in flight");
    expect(step("T-4")).toHaveTextContent("reserved for rev@alpha");
  });

  it("marks the accepted step accepted, and only that one", async () => {
    const { api } = fakeApi({ plan: PLAN });
    render(<App api={api} />);
    await waitFor(() => expect(screen.getAllByTestId("plan-step")).toHaveLength(6));
    expect(step("T-1")).toHaveTextContent("done, accepted");
    expect(step("T-2")).toHaveTextContent("done, not accepted");
    expect(within(step("T-2")).queryByText("done, accepted")).toBeNull();
  });

  it("never renders done-without-an-accept as accepted, even if the label says so", async () => {
    // A label claiming an accept the record does not carry is reported, not shown.
    const lying = node({
      id: "T-9",
      title: "Claims an accept it does not have",
      status: "review",
      status_label: "accepted by rev",
      accepted: false,
    });
    const { api } = fakeApi({ plan: { ...PLAN, nodes: [lying], layers: [["T-9"]], order: ["T-9"] } });
    render(<App api={api} />);
    const s = await waitFor(() => step("T-9"));
    expect(s).toHaveTextContent("review");
    expect(s).toHaveTextContent("This step is not accepted");
    expect(within(s).queryByText("accepted by rev")).toBeNull();
  });

  it("tells a dependency that is not accepted from one that is still open", async () => {
    const { api } = fakeApi({ plan: PLAN });
    render(<App api={api} />);
    await waitFor(() => expect(screen.getAllByTestId("plan-step")).toHaveLength(6));
    const unaccepted = within(step("T-5")).getByTestId("blockers");
    expect(unaccepted).toHaveTextContent("dependency not accepted");
    expect(unaccepted).toHaveTextContent("dep T-2 done, not accepted");
    expect(unaccepted).toHaveTextContent("atm accept T-2");

    const open = within(step("T-4")).getByTestId("blockers");
    expect(open).toHaveTextContent("dependency still open");
    expect(open).toHaveTextContent("dep T-3 still in flight");
    expect(open.textContent).not.toContain("dependency not accepted");
  });

  it("shows a limited seat, an offline seat and an auth blocker with their commands", async () => {
    const { api } = fakeApi({ plan: PLAN });
    render(<App api={api} />);
    await waitFor(() => expect(screen.getAllByTestId("plan-step")).toHaveLength(6));
    expect(within(step("T-4")).getByTestId("blockers")).toHaveTextContent("seat limited");
    expect(within(step("T-4")).getByTestId("blockers")).toHaveTextContent("limited until 17:40");
    const offline = within(step("T-6")).getByTestId("blockers");
    expect(offline).toHaveTextContent("seat offline");
    expect(offline).toHaveTextContent("atm spawn dave --persist");
    expect(offline).toHaveTextContent("auth");
    expect(offline).toHaveTextContent("atm auth reconnect dave");
  });

  it("marks what is running now, with its seat and elapsed time", async () => {
    const { api } = fakeApi({ plan: PLAN });
    render(<App api={api} />);
    await waitFor(() => expect(screen.getAllByTestId("plan-step")).toHaveLength(6));
    expect(step("T-3")).toHaveTextContent("running · coder@alpha · 14m");
  });

  it("opens the drill-down for the step that was clicked, and closes back to the plan", async () => {
    const user = userEvent.setup();
    const { api, calls } = fakeApi({ plan: PLAN, tickets: { "T-3": { id: "T-3", title: "In flight", status: "claimed", status_label: "in flight" } } });
    render(<App api={api} />);
    await waitFor(() => expect(screen.getAllByTestId("plan-step")).toHaveLength(6));
    await user.click(within(step("T-3")).getByRole("button", { name: /T-3/ }));
    expect(await screen.findByTestId("ticket-pane")).toHaveTextContent("In flight");
    expect(calls.some((c) => c.startsWith("ticket:T-3"))).toBe(true);
    await user.click(screen.getByRole("button", { name: "back to plan" }));
    expect(screen.queryByTestId("ticket-pane")).toBeNull();
    expect(screen.getAllByTestId("plan-step")).toHaveLength(6);
  });

  it("says the plan could not be built rather than showing an empty plan", async () => {
    const { api } = fakeApi({ plan: { available: false, nodes: [], layers: [], order: [] } });
    render(<App api={api} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("plan could not be built");
  });

  it("reports a failed plan read", async () => {
    const { api } = fakeApi({ fail: { plan: "no answer from the board" } });
    render(<App api={api} />);
    expect(await screen.findByText(/The plan could not be read/)).toBeInTheDocument();
  });
});
