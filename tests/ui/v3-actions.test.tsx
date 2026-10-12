/**
 * The buttons (spec §6): what each posts, what the result line says (the
 * API's answer, not the dialog's inputs), what a disabled verb says, the
 * stale-head guard before a verdict, and a refusal shown in the API's words.
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "../../ui/src/App";
import { fakeApi } from "./support/fake";
import { SHA, SHA2, STATES, TITLE, review, node } from "./support/v3";

function card(id: string): HTMLElement {
  const el = screen.getAllByTestId("card").find((c) => c.getAttribute("data-ticket") === id);
  if (!el) throw new Error(`no card for ${id}`);
  return el;
}

async function mount(parts: Parameters<typeof fakeApi>[0]) {
  const fake = fakeApi(parts);
  render(<App api={fake.api} />);
  await screen.findByTestId("strip");
  await waitFor(() => expect(screen.getAllByTestId("card").length).toBeGreaterThan(0));
  return fake;
}

describe("Accept", () => {
  it("re-reads the head, posts the full sha with notes, and shows what the API recorded", async () => {
    const fake = await mount({ ...STATES["needs-you"], action: () => ({ by: "rev", sha: SHA }) });
    const user = userEvent.setup();
    await user.click(within(card("T-001")).getByRole("button", { name: `Accept ${SHA.slice(0, 7)}` }));
    const dlg = await screen.findByTestId("dialog");
    await user.click(within(dlg).getByTestId("accept-submit"));
    // notes are required: nothing was posted
    expect(within(dlg).getByRole("alert")).toHaveTextContent("Notes are required");
    expect(fake.actions).toHaveLength(0);
    await user.type(within(dlg).getByLabelText("Notes (why this is accepted)"), "read schema.sql; tests pass");
    await user.click(within(dlg).getByTestId("accept-submit"));
    await waitFor(() => expect(fake.actions).toHaveLength(1));
    expect(fake.actions[0]).toEqual({ id: "T-001", action: "accept", body: { sha: SHA, notes: "read schema.sql; tests pass" }, project: "alpha" });
    // the ticket was re-read before the write (§6.2)
    expect(fake.calls.filter((c) => c.startsWith("ticket:T-001")).length).toBeGreaterThan(0);
    // the result line is the response: by "rev", not the operator "ada"
    const result = await within(card("T-001")).findByTestId("result");
    expect(result).toHaveTextContent(`Accepted · by rev · ${SHA.slice(0, 7)}`);
    expect(within(card("T-001")).queryByRole("button", { name: /Accept/ })).toBeNull();
    expect(screen.queryByTestId("dialog")).toBeNull();
  });

  it("shows the API's refusal in the dialog, in its words, and leaves the button usable", async () => {
    const fake = await mount({ ...STATES["needs-you"], action: "T-001: the ticket author (worker) cannot accept their own work" });
    const user = userEvent.setup();
    await user.click(within(card("T-001")).getByRole("button", { name: `Accept ${SHA.slice(0, 7)}` }));
    const dlg = await screen.findByTestId("dialog");
    await user.type(within(dlg).getByLabelText("Notes (why this is accepted)"), "ok");
    await user.click(within(dlg).getByTestId("accept-submit"));
    expect(await within(dlg).findByRole("alert")).toHaveTextContent("T-001: the ticket author (worker) cannot accept their own work");
    expect(within(dlg).getByTestId("accept-submit")).toBeEnabled();
    expect(fake.actions).toHaveLength(1);
    expect(within(card("T-001")).queryByTestId("result")).toBeNull();
  });

  it("refuses to post when the head changed since the dialog opened, and Review again redraws it", async () => {
    const fake = await mount({
      ...STATES["needs-you"],
      ticketLater: { "T-001": { ...STATES["needs-you"].tickets!["T-001"], review: { head: SHA2, head_len: 40, label: "Awaiting review of 9c1d2e3 · no verdict recorded", verified: false, verdicts: [] } } },
    });
    const user = userEvent.setup();
    await user.click(within(card("T-001")).getByRole("button", { name: `Accept ${SHA.slice(0, 7)}` }));
    const dlg = await screen.findByTestId("dialog");
    expect(within(dlg).getByTestId("accept-head")).toHaveTextContent(SHA);
    await user.type(within(dlg).getByLabelText("Notes (why this is accepted)"), "looks right");
    await user.click(within(dlg).getByTestId("accept-submit"));
    const stale = await within(dlg).findByTestId("stale");
    expect(stale).toHaveTextContent(`Changed since you opened this: new head ${SHA2.slice(0, 7)}`);
    expect(within(dlg).queryByTestId("accept-submit")).toBeNull();
    expect(fake.actions).toHaveLength(0);
    await user.click(within(dlg).getByRole("button", { name: "Review again" }));
    expect(within(dlg).getByTestId("accept-head")).toHaveTextContent(SHA2);
    expect(within(dlg).getByTestId("accept-submit")).toHaveTextContent(`Accept ${SHA2.slice(0, 7)}`);
  });

  it("catches a new head the poll brings while the dialog is open", async () => {
    vi.useFakeTimers();
    try {
      let reads = 0;
      const fake = fakeApi(STATES["needs-you"]);
      const api = fake.api as unknown as { plan: (p?: string) => Promise<unknown> };
      const original = api.plan.bind(api);
      api.plan = async (p?: string) => {
        reads += 1;
        const out = (await original(p)) as { nodes: ReturnType<typeof node>[] };
        if (reads > 1) out.nodes = [review("T-001", "worker", SHA2), ...out.nodes.slice(1)];
        return out;
      };
      render(<App api={fake.api} />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });
      fireEvent.click(within(card("T-001")).getByRole("button", { name: `Accept ${SHA.slice(0, 7)}` }));
      expect(screen.getByTestId("accept-submit")).toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(9000);
      });
      expect(screen.getByTestId("stale")).toHaveTextContent(SHA2.slice(0, 7));
      expect(screen.queryByTestId("accept-submit")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("Reject", () => {
  it("needs a reason, posts the sha, and says who it went back to", async () => {
    const fake = await mount({ ...STATES["needs-you"], action: () => ({ by: "ada", sha: SHA, returned_to: "worker", status: "claimed" }) });
    const user = userEvent.setup();
    await user.click(within(card("T-001")).getByRole("button", { name: "Reject…" }));
    const dlg = await screen.findByTestId("dialog");
    expect(within(dlg).getByRole("button", { name: "Reject and return to worker" })).toBeEnabled();
    await user.click(within(dlg).getByTestId("reject-submit"));
    expect(within(dlg).getByRole("alert")).toHaveTextContent("A reason is required");
    await user.type(within(dlg).getByLabelText("Reason (required)"), "edges overlap at 390");
    await user.click(within(dlg).getByTestId("reject-submit"));
    await waitFor(() => expect(fake.actions).toHaveLength(1));
    expect(fake.actions[0].body).toEqual({ sha: SHA, reason: "edges overlap at 390" });
    expect(await within(card("T-001")).findByTestId("result")).toHaveTextContent(`Rejected · by ada · ${SHA.slice(0, 7)} · back with worker`);
  });
});

describe("Assign", () => {
  it("never picks a seat, posts the chosen one with the why, and shows the assignment", async () => {
    const fake = await mount(STATES.fresh);
    const user = userEvent.setup();
    await user.click(within(card("T-001")).getByRole("button", { name: "Assign…" }));
    const dlg = await screen.findByTestId("dialog");
    expect(within(dlg).getByRole("radio", { name: /worker/ })).not.toBeChecked();
    await user.click(within(dlg).getByTestId("assign-submit"));
    expect(within(dlg).getByTestId("why")).toHaveTextContent("Pick a seat first. The app never picks one for you.");
    expect(fake.actions).toHaveLength(0);
    await user.click(within(dlg).getByRole("radio", { name: /worker/ }));
    expect(within(dlg).getByTestId("assign-submit")).toHaveTextContent("Assign to worker");
    await user.type(within(dlg).getByLabelText("Why (optional)"), "worker is free");
    await user.click(within(dlg).getByTestId("assign-submit"));
    await waitFor(() => expect(fake.actions).toHaveLength(1));
    expect(fake.actions[0]).toEqual({ id: "T-001", action: "assign", body: { owner: "worker", notes: "worker is free" }, project: "alpha" });
    expect(await within(card("T-001")).findByTestId("result")).toHaveTextContent("Assigned · to worker · by ada");
  });

  it("from the empty Working section offers the queued tickets and shows a limited seat with a warning", async () => {
    await mount({ ...STATES.fresh, board: STATES.offline.board });
    const user = userEvent.setup();
    await user.click(within(screen.getByTestId("empty-working")).getByRole("button", { name: "Assign…" }));
    const dlg = await screen.findByTestId("dialog");
    expect(within(dlg).getByRole("combobox", { name: "Ticket" })).toHaveValue("T-001");
    expect(within(dlg).getByText(/limited until 17:40 · will not start until then/)).toBeInTheDocument();
  });
});

describe("the result line", () => {
  it("stays until the next board read redraws the card in its new state", async () => {
    const fake = fakeApi({ ...STATES["needs-you"], action: () => ({ by: "ada", sha: SHA }) });
    const api = fake.api as unknown as { plan: (p?: string) => Promise<unknown> };
    const original = api.plan.bind(api);
    let accepted = false;
    api.plan = async (p?: string) => {
      const out = (await original(p)) as { nodes: ReturnType<typeof node>[]; counts: Record<string, number> };
      if (accepted) {
        out.nodes = [
          node("T-001", { status: "done", status_label: "done, accepted", phase: "done", owner: "worker", accepted: true, released: true, unverified: false, review: { verified: true, label: `Accepted by @ada on ${SHA.slice(0, 7)}`, head: SHA }, title: TITLE["T-001"] }),
          ...out.nodes.slice(1),
        ];
        out.counts = { ...out.counts, done: 1, review: 0 };
      }
      return out;
    };
    vi.useFakeTimers();
    try {
      render(<App api={fake.api} />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });
      fireEvent.click(within(card("T-001")).getByRole("button", { name: `Accept ${SHA.slice(0, 7)}` }));
      fireEvent.change(screen.getByLabelText("Notes (why this is accepted)"), { target: { value: "fine" } });
      accepted = true;
      fireEvent.click(screen.getByTestId("accept-submit"));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });
      // The line is up, and the card has not been redrawn: no poll has run yet.
      expect(screen.getByTestId("result")).toHaveTextContent(`Accepted · by ada · ${SHA.slice(0, 7)}`);
      expect(screen.getByTestId("section-needs")).toHaveTextContent("Needs you (2)");
      // The next poll brings the accepted card: it moves to Done and the line is gone.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(9000);
      });
      expect(screen.getByTestId("section-done")).toHaveTextContent("Done (1)");
      expect(screen.queryByTestId("result")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("Message and Answer", () => {
  it("focus the composer with the ticket and the owner set, and Send posts as the operator", async () => {
    const fake = await mount(STATES["needs-you"]);
    const user = userEvent.setup();
    await user.click(within(card("T-001")).getByRole("button", { name: "Message" }));
    expect(screen.getByRole("combobox", { name: "Talk to" })).toHaveValue("worker");
    expect(screen.getByRole("combobox", { name: "about" })).toHaveValue("T-001");
    const box = screen.getByRole("textbox", { name: /Message worker/ });
    await user.type(box, "ULIDs are fine{Enter}");
    await waitFor(() => expect(fake.posted).toHaveLength(1));
    expect(fake.posted[0]).toEqual({ from: "ada", text: "ULIDs are fine", to: "worker", re: "T-001" });
    expect(await screen.findByTestId("sent")).toHaveTextContent("Sent · to worker · about T-001 · posted");
    await user.click(within(screen.getByTestId("need-item")).getByRole("button", { name: "Answer" }));
    expect(screen.getByRole("combobox", { name: "Talk to" })).toHaveValue("worker");
  });
});
