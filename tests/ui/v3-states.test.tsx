/**
 * Every state in docs/design/app-v3.md §7, mounted: what the four questions
 * read as, which section each card is in, what its one sentence says, which
 * buttons it carries and why one is disabled. The copy is the spec's, so a
 * drift in wording fails here.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App } from "../../ui/src/App";
import { fakeApi } from "./support/fake";
import { OP, SHA, STATES, readOnly } from "./support/v3";

function card(id: string): HTMLElement {
  const el = screen.getAllByTestId("card").find((c) => c.getAttribute("data-ticket") === id);
  if (!el) throw new Error(`no card for ${id}`);
  return el;
}

async function mount(state: string, extra = {}) {
  const fake = fakeApi({ ...STATES[state], ...extra });
  render(<App api={fake.api} />);
  await screen.findByTestId("strip");
  await waitFor(() => expect(screen.queryByText("Reading the plan…")).toBeNull());
  return fake;
}

describe("empty", () => {
  it("says there is no objective and no tickets, with the setup buttons", async () => {
    await mount("empty");
    expect(screen.getByText("No objective yet. Say what this board is for.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set objective…" })).toBeEnabled();
    expect(screen.getByTestId("board-empty")).toHaveTextContent("This board has no tickets.");
    expect(screen.getByRole("button", { name: "Create ticket…" })).toBeEnabled();
    for (const k of ["working", "needs", "blocked", "done"]) expect(screen.getByTestId(`pill-${k}`)).toHaveTextContent(/^0 /);
    // worker's host is offline, and the strip says so rather than "idle"
    expect(screen.getByTestId("seat")).toHaveAttribute("data-presence", "offline");
    expect(screen.getByTestId("seat")).toHaveAttribute("title", "Adapter offline; directed work will remain queued.");
    expect(screen.getByRole("combobox", { name: "Talk to" })).toHaveValue("everyone");
  });
});

describe("fresh quickstart", () => {
  it("shows three samples in Up next, nobody working, and Assign… before Start an agent…", async () => {
    await mount("fresh");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Ship a small stats API with a screen on top");
    expect(screen.getByText(/^Done when the screen shows live stats/)).toBeInTheDocument();
    expect(screen.getByTestId("progress")).toHaveTextContent("0 of 3 accepted");
    expect(screen.getByTestId("empty-needs")).toHaveTextContent("Nothing needs you right now.");
    const working = screen.getByTestId("empty-working");
    expect(working).toHaveTextContent("Nobody is working. Assign a ticket or start an agent.");
    const buttons = within(working).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual(["Assign…", "Start an agent…"]);
    expect(buttons[0]).toHaveClass("primary");
    expect(buttons[1]).toHaveClass("quiet");
    expect(screen.getByTestId("empty-blocked")).toHaveTextContent("Nothing is blocked.");
    expect(within(card("T-001")).getByTestId("sentence")).toHaveTextContent("Nobody has it. Ready to start.");
    expect(within(card("T-002")).getByTestId("sentence")).toHaveTextContent("Nobody has it. Waits on T-001.");
    expect(within(card("T-003")).getByTestId("sentence")).toHaveTextContent("Nobody has it. Waits on T-002.");
    expect(within(card("T-001")).getByRole("button", { name: "Assign…" })).toBeEnabled();
    expect(screen.getByTestId("section-done")).toHaveTextContent("Done (0)");
    expect(screen.getByTestId("section-done")).toHaveTextContent("collapsed");
    expect(screen.getByRole("combobox", { name: "Talk to" })).toHaveValue("everyone");
    expect(screen.getByTestId("talk-state")).toHaveTextContent("No agent is running. Messages land on the board; the next agent that wakes reads them.");
  });
});

describe("working", () => {
  it("names who has it and for how long, and Talk opens with the one running seat", async () => {
    await mount("working");
    expect(screen.getByTestId("pill-working")).toHaveTextContent("1 working");
    expect(within(card("T-001")).getByTestId("sentence")).toHaveTextContent("worker has it, running for 12 min.");
    expect(card("T-001")).toHaveAttribute("data-bucket", "working");
    expect(screen.getByTestId("seat")).toHaveAttribute("data-presence", "working");
    expect(screen.getByTestId("seat")).toHaveTextContent("drafting the schema for T-001");
    expect(screen.getByRole("combobox", { name: "Talk to" })).toHaveValue("worker");
    expect(screen.getByTestId("talk-state")).toHaveTextContent("running T-001 for 12 min");
    expect(screen.getByRole("combobox", { name: "about" })).toHaveValue("T-001");
  });
});

describe("needs you", () => {
  it("lists the review card with its buttons and the question with Answer, under four filters", async () => {
    await mount("needs-you");
    expect(screen.getByTestId("pill-needs")).toHaveTextContent("2 needs you");
    expect(screen.getByTestId("pill-needs")).toHaveClass("hot");
    const section = screen.getByTestId("section-needs");
    expect(section).toHaveTextContent("Needs you (2)");
    const chips = within(section).getAllByRole("button").filter((b) => b.classList.contains("filter-chip"));
    expect(chips.map((c) => c.getAttribute("aria-label"))).toEqual(["All (2)", "Mentions (1)", "Accept (1)", "Blocked (0)"]);
    expect(chips[0]).toHaveAttribute("aria-pressed", "true");
    const c = card("T-001");
    expect(within(c).getByTestId("sentence")).toHaveTextContent("worker finished it 5 min ago. Waiting for your accept.");
    expect(within(c).getByTestId("review-line")).toHaveTextContent(`Review head ${SHA.slice(0, 7)} · schema.sql`);
    expect(within(c).getByRole("button", { name: `Accept ${SHA.slice(0, 7)}` })).toBeEnabled();
    expect(within(c).getByRole("button", { name: "Reject…" })).toBeEnabled();
    expect(within(c).getByRole("button", { name: "Message" })).toBeEnabled();
    const item = screen.getByTestId("need-item");
    expect(item).toHaveTextContent("worker asked 20 min ago about");
    expect(within(item).getByTestId("ticket-badge")).toHaveTextContent("T-001in review · worker");
    expect(within(item).getByRole("button", { name: "Answer" })).toBeEnabled();
    expect(within(card("T-002")).getByTestId("sentence")).toHaveTextContent("Nobody has it. Waits on T-001, which is in review.");
    // The chat shows the ticket as a badge wherever it is mentioned, and only for ids on the board
    const user = userEvent.setup();
    await user.selectOptions(screen.getByRole("combobox", { name: "Talk to" }), "worker");
  });

  it("filters without changing the count", async () => {
    await mount("needs-you");
    const user = userEvent.setup();
    const section = screen.getByTestId("section-needs");
    await user.click(within(section).getByRole("button", { name: "Accept (1)" }));
    expect(screen.getAllByTestId("card").filter((c) => c.closest("[data-testid=section-needs]"))).toHaveLength(1);
    expect(screen.queryByTestId("need-item")).toBeNull();
    expect(screen.getByTestId("pill-needs")).toHaveTextContent("2 needs you");
    expect(section).toHaveTextContent("Needs you (2)");
    await user.click(within(section).getByRole("button", { name: "Blocked (0)" }));
    expect(screen.getByTestId("empty-needs")).toHaveTextContent("Nothing under Blocked.");
    await user.click(within(section).getByRole("button", { name: "Mentions (1)" }));
    expect(screen.getByTestId("need-item")).toBeInTheDocument();
  });

  it("opens the Accept dialog with the full 40-character head and a required notes field", async () => {
    await mount("needs-you");
    const user = userEvent.setup();
    await user.click(within(card("T-001")).getByRole("button", { name: `Accept ${SHA.slice(0, 7)}` }));
    const dlg = await screen.findByTestId("dialog");
    expect(within(dlg).getByTestId("accept-head")).toHaveTextContent(SHA);
    expect(within(dlg).getByText(/schema\.sql, docs\/data-model\.md/)).toBeInTheDocument();
    expect(within(dlg).getByLabelText("Notes (why this is accepted)")).toBeEnabled();
    expect(within(dlg).queryByText(/accept latest|accept anyway/i)).toBeNull();
  });
});

describe("blocked", () => {
  it("says why in the owner's words with Answer and Reassign, and lists the ask under Needs you", async () => {
    await mount("blocked");
    expect(screen.getByTestId("pill-blocked")).toHaveTextContent("1 blocked");
    expect(screen.getByTestId("pill-done")).toHaveTextContent("1 done");
    const c = card("T-002");
    expect(c).toHaveAttribute("data-bucket", "blocked");
    expect(within(c).getByTestId("sentence")).toHaveTextContent("Blocked by worker 1.2 h ago: “needs staging DB credentials: no STAGING_DB_URL on this machine and I cannot mint one”");
    expect(within(c).getAllByRole("button").map((b) => b.textContent)).toEqual(["Open ›", "Answer", "Reassign…"]);
    const item = screen.getByTestId("need-item");
    expect(item).toHaveTextContent("worker has been stuck on");
    expect(within(item).getByRole("button", { name: "Reassign…" })).toBeEnabled();
    expect(within(card("T-003")).getByTestId("sentence")).toHaveTextContent("Nobody has it. Waits on T-002, which is blocked.");
    // astra's host is offline; the reason is the API's
    const astra = screen.getAllByTestId("seat").find((s) => s.textContent?.startsWith("astra"))!;
    expect(astra).toHaveAttribute("data-presence", "offline");
    expect(astra).toHaveTextContent("host offline");
    // T-001 sits in the collapsed Done
    expect(screen.getByTestId("section-done")).toHaveTextContent("Done (1)");
    expect(screen.queryByText(TITLE_T1)).toBeNull();
  });
});

const TITLE_T1 = "Sample: design the data model";

describe("accepted", () => {
  it("counts 1 of 3 accepted, names the proof once Done is opened, and shows what worker is doing", async () => {
    await mount("accepted");
    expect(screen.getByTestId("progress")).toHaveTextContent("1 of 3 accepted");
    expect(screen.getByTestId("pill-done")).toHaveTextContent("1 done");
    const worker = screen.getAllByTestId("seat").find((s) => s.textContent?.startsWith("worker"))!;
    expect(worker).toHaveTextContent("writing tests for the /stats endpoint for T-002");
    expect(within(card("T-002")).getByTestId("sentence")).toHaveTextContent("worker has it, running for 25 min.");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /^Done \(1\)$/ }));
    const c = card("T-001");
    expect(within(c).getByTestId("sentence")).toHaveTextContent(`Accepted by ${OP} 2 h ago on ${SHA.slice(0, 7)}.`);
    expect(within(c).getByTestId("acceptance-proof")).toHaveTextContent(`Accepted by @${OP} on ${SHA.slice(0, 7)}`);
    expect(within(c).getByTestId("acceptance-proof")).not.toHaveTextContent("no proof");
    expect(within(c).queryByRole("button", { name: /Accept/ })).toBeNull();
  });
});

describe("agent offline (limited)", () => {
  it("lists the limited seat under Needs you with Reassign, says so on the card, and Talk opens with everyone", async () => {
    await mount("offline");
    const item = screen.getByTestId("need-item");
    expect(item).toHaveTextContent(/^worker is limited until 17:40\. T-002.* is waiting on it\./);
    expect(within(item).getByTestId("ticket-badge")).toHaveTextContent("T-002");
    expect(within(item).getAllByRole("button").filter((b) => b.classList.contains("btn")).map((b) => b.textContent)).toEqual(["Reassign…", "Start another agent…", "Open T-002"]);
    const c = card("T-002");
    expect(c).toHaveAttribute("data-bucket", "blocked");
    expect(within(c).getByTestId("sentence")).toHaveTextContent("worker is limited until 17:40. Nothing runs on it until then.");
    expect(within(c).getAllByRole("button").map((b) => b.textContent)).toEqual(["Open ›", "Reassign…", "Message"]);
    const worker = screen.getAllByTestId("seat").find((s) => s.textContent?.startsWith("worker"))!;
    expect(worker).toHaveAttribute("data-presence", "limited");
    const astra = screen.getAllByTestId("seat").find((s) => s.textContent?.startsWith("astra"))!;
    expect(astra).toHaveAttribute("data-presence", "unknown");
    expect(screen.getByRole("combobox", { name: "Talk to" })).toHaveValue("everyone");
    const user = userEvent.setup();
    await user.selectOptions(screen.getByRole("combobox", { name: "Talk to" }), "worker");
    expect(screen.getByTestId("talk-state")).toHaveTextContent("limited until 17:40");
    expect(screen.getByText(/Your message is kept; it reads it when the limit lifts/)).toBeInTheDocument();
  });
});

describe("you are the author", () => {
  it("disables Accept with the reason beside it, on the card and in the dialog", async () => {
    await mount("self-review");
    const c = card("T-001");
    expect(within(c).getByTestId("sentence")).toHaveTextContent("You finished it 5 min ago. Another seat has to accept it.");
    const accept = within(c).getByRole("button", { name: `Accept ${SHA.slice(0, 7)}` });
    expect(accept).toBeDisabled();
    expect(within(c).getByTestId("why")).toHaveTextContent("You wrote this, so you can't accept it.");
    expect(within(c).getByRole("button", { name: "Reject…" })).toBeDisabled();
  });
});

describe("read-only", () => {
  it("says how to act and disables every verb with that reason", async () => {
    await mount("needs-you", readOnly(STATES["needs-you"]));
    expect(screen.getByTestId("read-only")).toHaveTextContent("Read-only. Start with atm ui --operator <name> to act.");
    const c = card("T-001");
    for (const b of within(c).getAllByRole("button").filter((x) => x.textContent !== "Open ›")) expect(b).toBeDisabled();
    expect(within(c).getByTestId("why")).toHaveTextContent("Read-only. Start with atm ui --operator <name> to act.");
    expect(screen.getByRole("textbox", { name: /Message/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    // reading still works
    expect(within(c).getByTestId("sentence")).toHaveTextContent("worker finished it 5 min ago. Waiting for your accept.");
  });
});

describe("details", () => {
  it("is off by default and puts diagnostics behind the toggle", async () => {
    await mount("needs-you");
    expect(screen.queryByTestId("card-details")).toBeNull();
    expect(screen.queryByTestId("receipts")).toBeNull();
    const user = userEvent.setup();
    await user.click(screen.getByRole("switch", { name: /Details/ }));
    expect(screen.getByRole("switch")).toHaveTextContent("Details on");
    const d = within(card("T-001")).getByTestId("card-details");
    expect(d).toHaveTextContent("phase review");
    expect(d).toHaveTextContent(`review head ${SHA}`);
    expect(d).toHaveTextContent(`git diff main...${SHA}`);
    expect(d).toHaveTextContent("atm show T-001");
    expect(screen.getByTestId("freshness")).toHaveTextContent(/Board read \d+s ago/);
  });
});

describe("the Done fold", () => {
  it("opens and closes from its header and keeps the count visible either way", async () => {
    await mount("accepted");
    const user = userEvent.setup();
    expect(screen.getByTestId("section-done")).toHaveTextContent("Done (1)");
    await user.click(screen.getByRole("button", { name: /^Done \(1\)$/ }));
    expect(screen.getByRole("button", { name: /^Done \(1\)$/ })).toHaveAttribute("aria-expanded", "true");
    expect(card("T-001")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Done \(1\)$/ }));
    expect(screen.getByTestId("section-done")).toHaveTextContent("collapsed");
  });
});
