import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MasterPanel } from "../../ui/src/screens/MasterPanel";
import { ToastHost } from "../../ui/src/components/Toast";

describe("MasterPanel", () => {
  it("shows the queue reservation copy as waiting for agent, not working", () => {
    render(<MasterPanel open onClose={() => {}} />);
    expect(screen.getByText(/Queued — waiting for agent/)).toBeInTheDocument();
  });

  it("does not claim to change routing when Pause/Run check are clicked (no fake live metrics)", async () => {
    const user = userEvent.setup();
    render(
      <>
        <MasterPanel open onClose={() => {}} />
        <ToastHost />
      </>,
    );
    await user.click(screen.getByRole("button", { name: "Pause assignments" }));
    expect(await screen.findByText(/Preview only/)).toBeInTheDocument();
    // Clicking must not silently flip the button to "Resume assignments" —
    // that would imply a write happened against a fixture-only view.
    expect(screen.getByRole("button", { name: "Pause assignments" })).toBeInTheDocument();
  });

  it("shows an unheld lease as blocking routing, not as idle", async () => {
    const user = userEvent.setup();
    render(<MasterPanel open onClose={() => {}} />);
    await user.selectOptions(screen.getByLabelText("Fixture scenario"), "unheld");
    expect(screen.getByText(/Unheld — no routing until a master takes over/)).toBeInTheDocument();
  });

  it("surfaces a no-eligible-agent decision with its reason, not just a blank queue", async () => {
    const user = userEvent.setup();
    render(<MasterPanel open onClose={() => {}} />);
    await user.selectOptions(screen.getByLabelText("Fixture scenario"), "no-eligible-agent");
    expect(screen.getByText(/Every capable agent is at capacity/)).toBeInTheDocument();
  });
});
