import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Tickets } from "../../ui/src/screens/Tickets";
import { formatDateTime } from "../../ui/src/copy";
import { boardFetch, renderLive } from "./support/render-live";
import ticketsPopulated from "../../ui/src/fixtures/data/tickets/list-populated.json";
import ticketsEmpty from "../../ui/src/fixtures/data/tickets/list-empty.json";
import detailQueued from "../../ui/src/fixtures/data/tickets/detail-queued-assignment.json";
import detailClaimed from "../../ui/src/fixtures/data/tickets/detail-claimed.json";

const listRoutes = { "GET /tickets": ticketsPopulated };

describe("Tickets, reading a live board", () => {
  it("marks a dependency-blocked ticket as waiting on its dependency, not as started", async () => {
    renderLive(<Tickets />, boardFetch(listRoutes));
    await waitFor(() => expect(screen.getByText("Waiting on DEMO-13")).toBeInTheDocument());
  });

  it("shows each ticket's updated_at in the host local timezone, not the raw UTC ISO", async () => {
    renderLive(<Tickets />, boardFetch(listRoutes));
    const first = ticketsPopulated.items[0];
    await waitFor(() => expect(screen.getByTestId(`ticket-updated-${first.id}`)).toBeInTheDocument());
    const cell = screen.getByTestId(`ticket-updated-${first.id}`);
    expect(cell).toHaveTextContent(formatDateTime(first.updated_at));
    expect(cell).not.toHaveTextContent(first.updated_at);
  });

  it("fetches the ticket from the board when its row is opened, and shows a reservation as waiting", async () => {
    const user = userEvent.setup();
    const harness = boardFetch({
      ...listRoutes,
      [`GET /tickets/${detailQueued.ticket.id}`]: detailQueued,
    });
    renderLive(<Tickets />, harness);

    await waitFor(() => expect(screen.getByTestId(`ticket-row-${detailQueued.ticket.id}`)).toBeInTheDocument());
    await user.click(within(screen.getByTestId(`ticket-row-${detailQueued.ticket.id}`)).getByRole("button"));

    expect(await screen.findByText(/Queued — waiting for agent/)).toBeInTheDocument();
    expect(screen.queryByText(/^Working$/)).not.toBeInTheDocument();
    // The detail came from the board, not from a bundled scenario.
    expect(harness.calls().some((c) => c.path === `/tickets/${detailQueued.ticket.id}`)).toBe(true);
  });

  it("shows ticket and note timestamps in local time on the detail dialog", async () => {
    const user = userEvent.setup();
    renderLive(
      <Tickets />,
      boardFetch({ ...listRoutes, [`GET /tickets/${detailClaimed.ticket.id}`]: detailClaimed }),
    );
    await waitFor(() => expect(screen.getByTestId(`ticket-row-${detailClaimed.ticket.id}`)).toBeInTheDocument());
    await user.click(within(screen.getByTestId(`ticket-row-${detailClaimed.ticket.id}`)).getByRole("button"));

    const stamps = await screen.findByTestId("ticket-timestamps");
    expect(stamps).toHaveTextContent(formatDateTime(detailClaimed.ticket.created_at));
    expect(stamps).toHaveTextContent(formatDateTime(detailClaimed.ticket.updated_at));
    expect(stamps).not.toHaveTextContent(detailClaimed.ticket.updated_at);

    const note = detailClaimed.updates[0];
    expect(screen.getByTestId(`update-time-${note.id}`)).toHaveTextContent(formatDateTime(note.created_at));
  });

  it("filters by state through the server, not by slicing the page it already has", async () => {
    const user = userEvent.setup();
    const harness = boardFetch(listRoutes);
    renderLive(<Tickets />, harness);
    await waitFor(() => expect(screen.getByTestId("total-matching")).toBeInTheDocument());

    await user.selectOptions(screen.getByLabelText("Filter by state"), "blocked");

    // A local filter over one page would silently search only what is loaded;
    // this asserts the query reached the board.
    await waitFor(() =>
      expect(harness.calls().some((c) => c.path === "/tickets" && c.method === "GET")).toBe(true),
    );
    const urls = harness.fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls.some((u) => u.includes("state=blocked"))).toBe(true);
  });

  it("renders the server's empty-state headline when the board returns no rows", async () => {
    renderLive(<Tickets />, boardFetch({ "GET /tickets": ticketsEmpty }));
    await waitFor(() => expect(screen.getByText(ticketsEmpty.empty_state.headline)).toBeInTheDocument());
  });

  it("dismisses the open detail dialog on Escape", async () => {
    const user = userEvent.setup();
    renderLive(
      <Tickets />,
      boardFetch({ ...listRoutes, [`GET /tickets/${detailClaimed.ticket.id}`]: detailClaimed }),
    );
    await waitFor(() => expect(screen.getByTestId(`ticket-row-${detailClaimed.ticket.id}`)).toBeInTheDocument());
    await user.click(within(screen.getByTestId(`ticket-row-${detailClaimed.ticket.id}`)).getByRole("button"));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("shows the id the board minted, never one it guessed, after creating a ticket", async () => {
    const user = userEvent.setup();
    const created = { ...detailClaimed.ticket, id: "DEMO-99", state: "open", version: 1 };
    const harness = boardFetch({ ...listRoutes, "POST /tickets": created });
    renderLive(<Tickets />, harness);

    await user.click(screen.getByTestId("new-ticket"));
    await user.type(screen.getByLabelText("Title"), "A new one");
    await user.type(screen.getByLabelText("Outcome"), "It exists");
    await user.type(screen.getByLabelText("Acceptance criterion"), "it is listed");
    await user.click(screen.getByTestId("create-ticket"));

    expect(await screen.findByTestId("create-result")).toHaveTextContent("DEMO-99");
    const post = harness.calls().find((c) => c.method === "POST" && c.path === "/tickets");
    const body = post!.body as Record<string, unknown>;
    // The contract mints ids server-side and CreateTicketRequest has no id
    // field; sending one would be rejected as an unexpected field.
    expect(body).not.toHaveProperty("id");
    expect(body).toHaveProperty("request_id");
    expect((body.acceptance as unknown[]).length).toBeGreaterThan(0);
  });

  it("refuses to send a create with no acceptance criterion rather than letting the board 422 it", async () => {
    const user = userEvent.setup();
    const harness = boardFetch(listRoutes);
    renderLive(<Tickets />, harness);

    await user.click(screen.getByTestId("new-ticket"));
    await user.type(screen.getByLabelText("Title"), "Incomplete");
    await user.type(screen.getByLabelText("Outcome"), "no criterion");

    expect(screen.getByTestId("create-ticket")).toBeDisabled();
    expect(harness.calls().some((c) => c.method === "POST")).toBe(false);
  });
});
