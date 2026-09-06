import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Agents } from "../../ui/src/screens/Agents";

describe("Agents", () => {
  it("shows both heartbeat and last-progress separately for a working agent", () => {
    render(<Agents onConnect={() => {}} />);
    expect(screen.getAllByText("Heartbeat").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Last progress").length).toBeGreaterThan(0);
  });

  it("notes that a hook-only agent cannot be woken by a message", async () => {
    const user = userEvent.setup();
    render(<Agents onConnect={() => {}} />);
    await user.selectOptions(screen.getByLabelText("Fixture scenario"), "hook-only");
    expect(screen.getByText(/Manual resume required/)).toBeInTheDocument();
  });

  it("shows the recovery note for a revoked lease instead of inferring death from silence", async () => {
    const user = userEvent.setup();
    render(<Agents onConnect={() => {}} />);
    await user.selectOptions(screen.getByLabelText("Fixture scenario"), "revoked");
    expect(screen.getByText(/Session unreachable for 12m/)).toBeInTheDocument();
  });

  it("distinguishes a probe (connection test) from real session adoption", async () => {
    const user = userEvent.setup();
    render(<Agents onConnect={() => {}} />);
    await user.selectOptions(screen.getByLabelText("Fixture scenario"), "probe-not-adopted");
    await user.click(screen.getByText("Hook health"));
    expect(screen.getByTestId("hook-health-session-adopted")).toHaveAttribute("data-ok", "false");
    expect(screen.getByTestId("hook-health-response-delivered")).toHaveAttribute("data-ok", "true");
  });
});
