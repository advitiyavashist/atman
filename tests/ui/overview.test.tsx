import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { Overview } from "../../ui/src/screens/Overview";
import { boardFetch, renderLive } from "./support/render-live";
import overviewPopulated from "../../ui/src/fixtures/data/overview/populated.json";
import overviewEmpty from "../../ui/src/fixtures/data/overview/empty.json";

describe("Overview, reading a live board", () => {
  it("renders counts it fetched from GET /overview, not from an imported fixture", async () => {
    const harness = boardFetch({ "GET /overview": overviewPopulated });
    renderLive(<Overview onNavigate={() => {}} onOpenMasterPanel={() => {}} />, harness);

    await waitFor(() => expect(screen.getByTestId("count-ready")).toHaveTextContent(String(overviewPopulated.counts.ready)));
    expect(harness.calls().some((c) => c.method === "GET" && c.path === "/overview")).toBe(true);
    expect(screen.getByTestId("count-blocked")).toHaveTextContent(String(overviewPopulated.counts.blocked));
  });

  it("renders the server's empty-state headline verbatim", async () => {
    const harness = boardFetch({ "GET /overview": overviewEmpty });
    renderLive(<Overview onNavigate={() => {}} onOpenMasterPanel={() => {}} />, harness);
    await waitFor(() => expect(screen.getByText(overviewEmpty.empty_state.headline)).toBeInTheDocument());
    expect(screen.getByTestId("day-one-path")).toBeInTheDocument();
    expect(screen.getByTestId("day-one-path").textContent).toMatch(/Objective · Team · Work · Intervene/);
    expect(screen.getByTestId("day-one-path").querySelectorAll("li")).toHaveLength(3);
  });

  it("shows nothing from the board when the read fails, and says so", async () => {
    const harness = boardFetch({
      "GET /overview": new Response(
        JSON.stringify({ error: { code: "unauthenticated", status: 401, message: "Session expired. Sign in again." } }),
        { status: 401, headers: { "Content-Type": "application/json" } },
      ),
    });
    renderLive(<Overview onNavigate={() => {}} onOpenMasterPanel={() => {}} />, harness);

    await waitFor(() => expect(screen.getByTestId("error-notice")).toBeInTheDocument());
    // The failure must not be dressed as an empty board: no zeroes anywhere.
    expect(screen.queryByTestId("count-ready")).not.toBeInTheDocument();
    expect(screen.getByTestId("error-notice")).toHaveAttribute("data-error-code", "unauthenticated");
  });

  it("never claims a latency number or a competitor comparison", async () => {
    const harness = boardFetch({ "GET /overview": overviewPopulated });
    const { container } = renderLive(<Overview onNavigate={() => {}} onOpenMasterPanel={() => {}} />, harness);
    await waitFor(() => expect(screen.getByTestId("count-ready")).toBeInTheDocument());
    expect(container.textContent).not.toMatch(/50\s*ms/i);
    expect(container.textContent).not.toMatch(/presidio/i);
  });

  it("no longer carries the fixture-mode disclaimer, because it is no longer true", async () => {
    const harness = boardFetch({ "GET /overview": overviewPopulated });
    const { container } = renderLive(<Overview onNavigate={() => {}} onOpenMasterPanel={() => {}} />, harness);
    await waitFor(() => expect(screen.getByTestId("count-ready")).toBeInTheDocument());
    expect(container.textContent).not.toMatch(/fixture/i);
    expect(container.textContent).not.toMatch(/not live board state/i);
  });
});
