import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Tickets } from "../../ui/src/screens/Tickets";

describe("Tickets", () => {
  it("marks a dependency-blocked ticket as Waiting on its dependency, not as started", () => {
    render(<Tickets />);
    expect(screen.getByText("Waiting on DEMO-13")).toBeInTheDocument();
  });

  it("opens the detail dialog for a ticket and shows a queued reservation as waiting, not working", async () => {
    const user = userEvent.setup();
    render(<Tickets />);
    await user.click(screen.getByRole("button", { name: /Document the widget endpoint/i }));
    expect(await screen.findByText("Queued — waiting for agent")).toBeInTheDocument();
    expect(screen.queryByText(/^Working$/)).not.toBeInTheDocument();
  });

  it("filters rows by search text", async () => {
    const user = userEvent.setup();
    render(<Tickets />);
    await user.type(screen.getByLabelText("Search tickets"), "DEMO-16");
    expect(screen.getByText("DEMO-16")).toBeInTheDocument();
    expect(screen.queryByText("DEMO-13")).not.toBeInTheDocument();
  });

  it("renders the server empty-state headline when the fixture has no rows", async () => {
    const user = userEvent.setup();
    render(<Tickets />);
    await user.selectOptions(screen.getByLabelText("Fixture scenario"), "empty");
    expect(screen.getByText("No tickets match this filter.")).toBeInTheDocument();
  });

  it("dismisses the open detail dialog on Escape", async () => {
    const user = userEvent.setup();
    render(<Tickets />);
    await user.click(screen.getByRole("button", { name: /Add the widget endpoint/i }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
