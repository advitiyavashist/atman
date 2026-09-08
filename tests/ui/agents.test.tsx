import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Agents } from "../../ui/src/screens/Agents";
import { boardFetch, renderLive } from "./support/render-live";
import agentsPopulated from "../../ui/src/fixtures/data/agents/list-populated.json";
import agentsHookOnly from "../../ui/src/fixtures/data/agents/list-hook-only.json";
import agentsRevoked from "../../ui/src/fixtures/data/agents/list-revoked.json";
import agentsProbe from "../../ui/src/fixtures/data/agents/list-probe-not-adopted.json";

describe("Agents, reading a live board", () => {
  it("shows heartbeat and last progress separately", async () => {
    renderLive(<Agents onConnect={() => {}} />, boardFetch({ "GET /agents": agentsPopulated }));
    await waitFor(() => expect(screen.getAllByText("Heartbeat").length).toBeGreaterThan(0));
    expect(screen.getAllByText("Last progress").length).toBeGreaterThan(0);
  });

  it("notes that a hook-only agent cannot be woken by a message", async () => {
    renderLive(<Agents onConnect={() => {}} />, boardFetch({ "GET /agents": agentsHookOnly }));
    await waitFor(() => expect(screen.getByText(/Manual resume required/)).toBeInTheDocument());
  });

  it("shows a revoked lease as an explicit act with its note, not as inferred silence", async () => {
    renderLive(<Agents onConnect={() => {}} />, boardFetch({ "GET /agents": agentsRevoked }));
    await waitFor(() => expect(screen.getByText(/Session unreachable for 12m/)).toBeInTheDocument());
  });

  it("distinguishes a delivered probe from real session adoption", async () => {
    renderLive(<Agents onConnect={() => {}} />, boardFetch({ "GET /agents": agentsProbe }));
    await waitFor(() =>
      expect(screen.getByTestId("hook-health-session-adopted")).toHaveAttribute("data-ok", "false"),
    );
    expect(screen.getByTestId("hook-health-response-delivered")).toHaveAttribute("data-ok", "true");
  });

  it("names the step the connection stopped at rather than only ticking boxes", async () => {
    renderLive(<Agents onConnect={() => {}} />, boardFetch({ "GET /agents": agentsProbe }));
    const agentId = agentsProbe.items[0].id;
    await waitFor(() => expect(screen.getByTestId(`hook-stopped-at-${agentId}`)).toBeInTheDocument());
    expect(screen.getByTestId(`hook-stopped-at-${agentId}`)).toHaveTextContent("Session adopted");
  });

  it("does not offer to revoke a lease that is already revoked", async () => {
    renderLive(<Agents onConnect={() => {}} />, boardFetch({ "GET /agents": agentsRevoked }));
    await waitFor(() => expect(screen.getByText(/Session unreachable for 12m/)).toBeInTheDocument());
    expect(screen.queryByTestId(`revoke-open-${agentsRevoked.items[0].id}`)).not.toBeInTheDocument();
  });

  it("sends the agent's version and a note when revoking, and reports the board's answer", async () => {
    const user = userEvent.setup();
    const target = agentsPopulated.items.find((a) => a.session && !a.session.revoked_at)!;
    const harness = boardFetch({
      "GET /agents": agentsPopulated,
      [`DELETE /agents/${target.id}/session-lease`]: { ...target, state: "revoked" },
    });
    renderLive(<Agents onConnect={() => {}} />, harness);

    await waitFor(() => expect(screen.getByTestId(`revoke-open-${target.id}`)).toBeInTheDocument());
    await user.click(screen.getByTestId(`revoke-open-${target.id}`));
    // A revocation with no recorded reason is not auditable, so the control
    // refuses before the board has to.
    expect(screen.getByTestId(`revoke-confirm-${target.id}`)).toBeDisabled();

    await user.type(screen.getByLabelText("Why (recorded on the agent)"), "stalled for 20m");
    await user.click(screen.getByTestId(`revoke-confirm-${target.id}`));

    await waitFor(() => expect(screen.getByTestId(`revoke-result-${target.id}`)).toBeInTheDocument());
    const call = harness.calls().find((c) => c.method === "DELETE")!;
    const body = call.body as Record<string, unknown>;
    expect(body.expected_version).toBe(target.version);
    expect(body.note).toBe("stalled for 20m");
    // The screen reports the state the board returned, not the one it wanted.
    expect(screen.getByTestId(`revoke-result-${target.id}`)).toHaveTextContent("Revoked");
  });

  it("says a revoke that the board refused did not happen", async () => {
    const user = userEvent.setup();
    const target = agentsPopulated.items.find((a) => a.session && !a.session.revoked_at)!;
    const harness = boardFetch({
      "GET /agents": agentsPopulated,
      [`DELETE /agents/${target.id}/session-lease`]: new Response(
        JSON.stringify({
          error: {
            code: "session_lease_expired",
            status: 409,
            message: "Session lease already revoked.",
            details: { reason: "already revoked" },
          },
        }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ),
    });
    renderLive(<Agents onConnect={() => {}} />, harness);

    await waitFor(() => expect(screen.getByTestId(`revoke-open-${target.id}`)).toBeInTheDocument());
    await user.click(screen.getByTestId(`revoke-open-${target.id}`));
    await user.type(screen.getByLabelText("Why (recorded on the agent)"), "again");
    await user.click(screen.getByTestId(`revoke-confirm-${target.id}`));

    await waitFor(() => expect(screen.getByTestId("error-notice")).toBeInTheDocument());
    // No success line may appear alongside the refusal.
    expect(screen.queryByTestId(`revoke-result-${target.id}`)).not.toBeInTheDocument();
  });
});
