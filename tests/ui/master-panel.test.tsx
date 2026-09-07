import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MasterPanel } from "../../ui/src/screens/MasterPanel";
import { boardFetch, renderLive } from "./support/render-live";
import masterActive from "../../ui/src/fixtures/data/master/panel-active.json";
import masterUnheld from "../../ui/src/fixtures/data/master/panel-unheld.json";
import masterNoEligible from "../../ui/src/fixtures/data/master/panel-no-eligible-agent.json";
import ticketsPopulated from "../../ui/src/fixtures/data/tickets/list-populated.json";
import agentsPopulated from "../../ui/src/fixtures/data/agents/list-populated.json";

const base = {
  "GET /tickets": ticketsPopulated,
  "GET /agents": agentsPopulated,
};

describe("MasterPanel, reading a live board", () => {
  it("shows a queued reservation as waiting for an agent, never as working", async () => {
    renderLive(<MasterPanel open onClose={() => {}} />, boardFetch({ ...base, "GET /master": masterActive }));
    await waitFor(() => expect(screen.getAllByText(/Queued — waiting for agent/).length).toBeGreaterThan(0));
  });

  it("shows an unheld lease as blocking routing, not as idle", async () => {
    renderLive(<MasterPanel open onClose={() => {}} />, boardFetch({ ...base, "GET /master": masterUnheld }));
    await waitFor(() =>
      expect(screen.getByText(/Unheld — no routing until a master takes over/)).toBeInTheDocument(),
    );
  });

  it("surfaces a no-eligible-agent decision with its reason, not just a blank queue", async () => {
    renderLive(<MasterPanel open onClose={() => {}} />, boardFetch({ ...base, "GET /master": masterNoEligible }));
    await waitFor(() => expect(screen.getByText(/Every capable agent is at capacity/)).toBeInTheDocument());
  });

  it("fences a pause on the epoch it is showing the operator", async () => {
    const user = userEvent.setup();
    const harness = boardFetch({
      ...base,
      "GET /master": masterActive,
      "POST /master/pause": { ...masterActive.lease, paused: true },
    });
    renderLive(<MasterPanel open onClose={() => {}} />, harness);

    await waitFor(() => expect(screen.getByTestId("toggle-pause")).toBeInTheDocument());
    expect(screen.getByTestId("lease-epoch")).toHaveTextContent(String(masterActive.lease.epoch));
    await user.click(screen.getByTestId("toggle-pause"));

    await waitFor(() => expect(harness.calls().some((c) => c.path === "/master/pause")).toBe(true));
    const body = harness.calls().find((c) => c.path === "/master/pause")!.body as Record<string, unknown>;
    // The epoch sent is the one on screen: that is what makes the fence real
    // rather than a number the client refreshed behind the operator's back.
    expect(body.lease_epoch).toBe(masterActive.lease.epoch);
    expect(body.paused).toBe(true);
  });

  it("does not flip the pause label when the board refuses the write", async () => {
    const user = userEvent.setup();
    const harness = boardFetch({
      ...base,
      "GET /master": masterActive,
      "POST /master/pause": new Response(
        JSON.stringify({
          error: { code: "master_lease_conflict", status: 409, message: "Another master holds the lease." },
        }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ),
    });
    renderLive(<MasterPanel open onClose={() => {}} />, harness);

    await waitFor(() => expect(screen.getByTestId("toggle-pause")).toBeInTheDocument());
    await user.click(screen.getByTestId("toggle-pause"));

    await waitFor(() => expect(screen.getByTestId("error-notice")).toBeInTheDocument());
    // Still says Pause: the board refused, so nothing about the board changed.
    expect(screen.getByTestId("toggle-pause")).toHaveTextContent("Pause assignments");
    expect(screen.getByTestId("lease-paused")).toHaveTextContent("No");
  });

  it("will not reserve a ticket without a recorded reason", async () => {
    const user = userEvent.setup();
    const harness = boardFetch({ ...base, "GET /master": masterActive });
    renderLive(<MasterPanel open onClose={() => {}} />, harness);

    // Wait for the option itself, not merely the button: the ticket and agent
    // lists are their own reads and land after the panel renders.
    await waitFor(() =>
      expect(screen.getByLabelText("Ticket")).toHaveTextContent(ticketsPopulated.items[0].id),
    );
    await waitFor(() => expect(screen.getByLabelText("Agent")).toHaveTextContent(agentsPopulated.items[0].name));
    await user.selectOptions(screen.getByLabelText("Ticket"), ticketsPopulated.items[0].id);
    await user.selectOptions(screen.getByLabelText("Agent"), agentsPopulated.items[0].id);
    expect(screen.getByTestId("create-assignment")).toBeDisabled();

    await user.type(screen.getByLabelText(/Reason/), "closest match by role");
    expect(screen.getByTestId("create-assignment")).toBeEnabled();
  });

  it("sends both fences on a reservation: the lease epoch and the ticket's own version", async () => {
    const user = userEvent.setup();
    const ticket = ticketsPopulated.items[0];
    const agent = agentsPopulated.items[0];
    const harness = boardFetch({
      ...base,
      "GET /master": masterActive,
      // Shaped from the contract's own Assignment fixture rather than typed by
      // hand — an invented stub here silently drifts from what the board sends,
      // and the client's response validation catches it as invalid_response_shape.
      "POST /assignments": {
        ...masterActive.queue[0],
        ticket_id: ticket.id,
        agent_id: agent.id,
        reason: "closest match by role",
      },
    });
    renderLive(<MasterPanel open onClose={() => {}} />, harness);

    await waitFor(() => expect(screen.getByLabelText("Ticket")).toHaveTextContent(ticket.id));
    await waitFor(() => expect(screen.getByLabelText("Agent")).toHaveTextContent(agent.name));
    await user.selectOptions(screen.getByLabelText("Ticket"), ticket.id);
    await user.selectOptions(screen.getByLabelText("Agent"), agent.id);
    await user.type(screen.getByLabelText(/Reason/), "closest match by role");
    await user.click(screen.getByTestId("create-assignment"));

    await waitFor(() => expect(screen.getByTestId("assign-result")).toBeInTheDocument());
    const body = harness.calls().find((c) => c.path === "/assignments")!.body as Record<string, unknown>;
    expect(body.lease_epoch).toBe(masterActive.lease.epoch);
    expect(body.expected_version).toBe(ticket.version);
    expect(body.reason).toBe("closest match by role");
    // And the result reads as a reservation, not as work under way.
    expect(screen.getByTestId("assign-result")).toHaveTextContent("Queued — waiting for agent");
  });
});
