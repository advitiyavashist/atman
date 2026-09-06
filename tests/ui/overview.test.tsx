import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Overview } from "../../ui/src/screens/Overview";
import overviewEmpty from "../../ui/src/fixtures/data/overview/empty.json";

describe("Overview", () => {
  it("renders counts from the populated fixture, labeled as fixture data", () => {
    render(<Overview onNavigate={() => {}} onOpenMasterPanel={() => {}} />);
    expect(screen.getByText("Overview")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument(); // ready count
    expect(screen.getByText(/not live board state/i)).toBeInTheDocument();
  });

  it("renders the server-supplied empty-state headline verbatim when switched to it", async () => {
    const user = userEvent.setup();
    render(<Overview onNavigate={() => {}} onOpenMasterPanel={() => {}} />);
    await user.selectOptions(screen.getByLabelText("Fixture scenario"), "empty");
    expect(screen.getByText(overviewEmpty.empty_state.headline)).toBeInTheDocument();
  });

  it("shows the stale-connection banner copy when that scenario is selected", async () => {
    const user = userEvent.setup();
    render(<Overview onNavigate={() => {}} onOpenMasterPanel={() => {}} />);
    await user.selectOptions(screen.getByLabelText("Fixture scenario"), "stale-stream");
    expect(screen.getByText(/Connection lost\. Showing updates from/)).toBeInTheDocument();
  });

  it("never claims sub-50ms or a live comparison anywhere on the screen", () => {
    const { container } = render(<Overview onNavigate={() => {}} onOpenMasterPanel={() => {}} />);
    expect(container.textContent).not.toMatch(/50\s*ms/i);
    expect(container.textContent).not.toMatch(/presidio/i);
  });
});
