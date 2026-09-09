import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { EmptyStateView } from "../../ui/src/components/EmptyStateView";
import activityEmpty from "../../ui/src/fixtures/data/activity/empty.json";
import agentsEmpty from "../../ui/src/fixtures/data/agents/list-empty.json";

describe("EmptyStateView craft", () => {
  it("renders icon, headline, helper, and muted honesty from server copy", () => {
    render(<EmptyStateView empty={activityEmpty.empty_state} icon="☰" />);
    expect(screen.getByTestId("empty-state")).toHaveClass("empty-block");
    expect(screen.getByText("☰")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(activityEmpty.empty_state.headline);
    expect(screen.getByText("This filter matches no audit events.")).toHaveClass("empty-state-detail");
    expect(screen.getByText("Traffic is never invented — only recorded events appear.")).toHaveClass("empty-honesty");
  });

  it("renders the primary action when the board supplies one", () => {
    render(<EmptyStateView empty={agentsEmpty.empty_state} icon="◎" onPrimaryAction={() => {}} />);
    expect(screen.getByRole("button", { name: agentsEmpty.empty_state.primary_action! })).toBeInTheDocument();
    expect(screen.getByText("Run tickets join <name> --roles … from the harness machine.")).toBeInTheDocument();
    expect(screen.getByText("Seats stay unmeasured until the first heartbeat.")).toHaveClass("empty-honesty");
  });
});
