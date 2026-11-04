/**
 * The Talk column and the composer (spec §4, §5.6, §5.9): the recipient
 * rule, Enter / Shift+Enter / IME, autocomplete that never sends, drafts kept
 * per ticket, ticket badges only for tickets on this board, and the panel's
 * own thread and composer.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App } from "../../ui/src/App";
import type { Post } from "../../ui/src/api/types";
import { fakeApi } from "./support/fake";
import { defFixture } from "./support/schema";
import { STATES, stamp } from "./support/v3";

function post(over: Partial<Post>): Post {
  return defFixture<Post>("post", { id: "m1", at: stamp(10), from: "worker", author: "worker@alpha", to: "ada", text: "hello", ...over } as never);
}

async function mount(parts: Parameters<typeof fakeApi>[0]) {
  const fake = fakeApi(parts);
  render(<App api={fake.api} />);
  await screen.findByTestId("strip");
  await waitFor(() => expect(screen.queryByText("Reading the plan…")).toBeNull());
  return fake;
}

describe("the composer", () => {
  it("sends on Enter and adds a line on Shift+Enter", async () => {
    const fake = await mount(STATES.working);
    const user = userEvent.setup();
    const box = screen.getByRole("textbox", { name: /Message worker/ });
    await user.type(box, "line one{Shift>}{Enter}{/Shift}line two");
    expect(box).toHaveValue("line one\nline two");
    expect(fake.posted).toHaveLength(0);
    await user.type(box, "{Enter}");
    await waitFor(() => expect(fake.posted).toHaveLength(1));
    expect(fake.posted[0].text).toBe("line one\nline two");
    expect(box).toHaveValue("");
  });

  it("does not send on Enter during an IME composition, nor with nothing typed", async () => {
    const fake = await mount(STATES.working);
    const user = userEvent.setup();
    const box = screen.getByRole("textbox", { name: /Message worker/ });
    await user.type(box, "{Enter}");
    expect(fake.posted).toHaveLength(0);
    await user.type(box, "日本");
    fireEvent.keyDown(box, { key: "Enter", isComposing: true });
    fireEvent.keyDown(box, { key: "Enter", keyCode: 229 });
    expect(fake.posted).toHaveLength(0);
    expect(box).toHaveValue("日本");
  });

  it("keeps a draft per ticket and swaps it when about changes", async () => {
    await mount(STATES["needs-you"]);
    const user = userEvent.setup();
    const box = screen.getByRole("textbox", { name: /Message/ });
    const about = screen.getByRole("combobox", { name: "about" });
    await user.type(box, "about nothing");
    expect(screen.getByTestId("draft")).toHaveTextContent("Draft");
    await user.selectOptions(about, "T-002");
    expect(box).toHaveValue("");
    await user.type(box, "about two");
    await user.selectOptions(about, "");
    expect(box).toHaveValue("about nothing");
    await user.selectOptions(about, "T-002");
    expect(box).toHaveValue("about two");
  });

  it("offers seats on @ and tickets on #; Enter picks and never sends; # sets about", async () => {
    const fake = await mount(STATES["needs-you"]);
    const user = userEvent.setup();
    const box = screen.getByRole("textbox", { name: /Message/ });
    await user.type(box, "@");
    const list = await screen.findByTestId("autocomplete");
    expect(within(list).getAllByRole("option").map((o) => o.textContent)).toEqual(["everyonethe whole board", "workeridle 5 min", "astraidle 2 h"]);
    await user.keyboard("wo{Enter}");
    expect(fake.posted).toHaveLength(0);
    expect(box).toHaveValue("@worker ");
    expect(screen.queryByTestId("autocomplete")).toBeNull();
    await user.keyboard("see #");
    expect(box).toHaveValue("@worker see #");
    const tickets = await screen.findByTestId("autocomplete");
    expect(within(tickets).getAllByRole("option")[0]).toHaveTextContent("T-001Sample: design the data model · in review · worker");
    await user.keyboard("{ArrowDown}");
    expect(within(screen.getByTestId("autocomplete")).getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{Enter}");
    expect(box).toHaveValue("@worker see #T-002 ");
    expect(screen.getByRole("combobox", { name: "about" })).toHaveValue("T-002");
    expect(fake.posted).toHaveLength(0);
  });
});

describe("the thread", () => {
  it("shows the seat thread with ticket badges only for tickets on this board, and receipts behind Details", async () => {
    await mount({
      ...STATES.working,
      thread: {
        with: "worker",
        messages: [
          post({ id: "m1", from: "ada", author: "ada@alpha", to: "worker", text: "hi", operator: true, harness: { value: "operator", recorded: true, note: "" }, receipts: [{ agent: "worker", words: ["posted", "inbox read"] }] }),
          post({ id: "m2", at: stamp(5), text: "claimed #T-001; T-999 is someone else's. Three tables.", harness: { value: "unknown", recorded: false, note: "no harness recorded for this post" } }),
        ],
      },
    });
    const thread = screen.getByTestId("thread");
    const rows = within(thread).getAllByTestId("message");
    expect(rows).toHaveLength(2);
    expect(within(rows[1]).getByTestId("ticket-badge")).toHaveTextContent("T-001working · worker");
    expect(rows[1]).toHaveTextContent("T-999 is someone else's");
    expect(within(rows[1]).getAllByTestId("ticket-badge")).toHaveLength(1);
    expect(screen.queryByTestId("receipts")).toBeNull();
    const user = userEvent.setup();
    await user.click(screen.getByRole("switch", { name: /Details/ }));
    expect(within(rows[0]).getByTestId("receipts")).toHaveTextContent("postedinbox read");
    expect(within(rows[1]).getByTestId("harness-badge")).toHaveTextContent("harness unknown · not recorded");
  });

  it("clicking a badge opens that ticket's panel with its own thread and a composer fixed to it", async () => {
    const fake = await mount({
      ...STATES["needs-you"],
      thread: { with: "worker", messages: [post({ id: "m2", text: "review: #T-001 at 3f2a1c0", re: "T-001" })] },
      threadsRe: { "T-001": { messages: [post({ id: "m2", text: "review: #T-001 at 3f2a1c0", re: "T-001" }), post({ id: "m3", from: "ada", author: "ada@alpha", to: "worker", re: "T-001", text: "looking" })] } },
    });
    const user = userEvent.setup();
    await user.selectOptions(screen.getByRole("combobox", { name: "Talk to" }), "worker");
    await user.click(await within(screen.getByTestId("thread")).findByTestId("ticket-badge"));
    const panel = await screen.findByTestId("ticket-panel");
    expect(panel).toHaveTextContent("Sample: design the data model");
    expect(within(panel).getByTestId("panel-sentence")).toHaveTextContent("worker finished it 5 min ago. Waiting for your accept.");
    expect(within(panel).getByRole("button", { name: /^Accept / })).toBeEnabled();
    expect(await within(within(panel).getByTestId("panel-thread")).findAllByTestId("message")).toHaveLength(2);
    expect(within(panel).getByTestId("review-head")).toHaveTextContent(/^[0-9a-f]{40}$/);
    expect(within(panel).getByTestId("acceptance-proof")).toHaveTextContent("None yet. Your accept, with its notes, becomes the proof.");
    const box = within(panel).getByRole("textbox", { name: /Message worker about T-001/ });
    await user.type(box, "ULIDs are fine{Enter}");
    await waitFor(() => expect(fake.posted).toHaveLength(1));
    expect(fake.posted[0]).toEqual({ from: "ada", text: "ULIDs are fine", to: "worker", re: "T-001" });
    expect(location.hash).toBe("#T-001");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByTestId("ticket-panel")).toBeNull());
  });

  it("never shows one ticket's evidence under another id when the read fails", async () => {
    await mount({ ...STATES["needs-you"] });
    const user = userEvent.setup();
    const card = (id: string) => screen.getAllByTestId("card").find((c) => c.getAttribute("data-ticket") === id)!;
    await user.click(within(card("T-001")).getByRole("button", { name: "Open T-001" }));
    const panel = await screen.findByTestId("ticket-panel");
    await within(panel).findByTestId("review-head");
    // T-002 has no entry in `tickets`, so the fake refuses it like the server would
    await user.keyboard("{Escape}");
    await user.click(within(card("T-002")).getByRole("button", { name: "Open T-002" }));
    const next = await screen.findByTestId("ticket-panel");
    expect(await within(next).findByText(/The ticket could not be read/)).toBeInTheDocument();
    expect(within(next).queryByTestId("review-head")).toBeNull();
    expect(next).not.toHaveTextContent("3f2a1c0d9e8b");
    expect(screen.getByTestId("freshness")).toHaveTextContent("last read failed: ticket");
  });
});

describe("the recipient", () => {
  it("is the board's lead when one is set, and Set as lead records a choice", async () => {
    const fake = await mount({ ...STATES["needs-you"], board: { ...STATES["needs-you"].board, app: { project: "alpha", operator: "ada", operator_note: "", lead: "astra", lead_note: "" } } });
    expect(screen.getByRole("combobox", { name: "Talk to" })).toHaveValue("astra");
    const user = userEvent.setup();
    await user.selectOptions(screen.getByRole("combobox", { name: "Talk to" }), "worker");
    await user.click(screen.getByRole("button", { name: "Set as lead" }));
    await waitFor(() => expect(fake.leadSet).toEqual(["worker"]));
  });

  it("clicking a seat in the presence strip opens Talk with it", async () => {
    await mount(STATES["needs-you"]);
    const user = userEvent.setup();
    await user.click(screen.getAllByTestId("seat").find((s) => s.textContent?.startsWith("astra"))!);
    expect(screen.getByRole("combobox", { name: "Talk to" })).toHaveValue("astra");
    expect(screen.getByTestId("talk-state")).toHaveTextContent("idle since 2 h");
    expect(screen.getByText(/astra is not running. Your message is kept; it reads it when it starts./)).toBeInTheDocument();
  });
});
