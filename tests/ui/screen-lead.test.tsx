/**
 * The chat screen, mounted.
 *
 * What an operator has to be able to tell apart here: who posted (seat@project),
 * on which harness (and whether that was recorded at all), when in their own
 * clock, whether the message was *delivered* — not agreed to — and whether the
 * lead can answer right now.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App } from "../../ui/src/App";
import { LeadPane } from "../../ui/src/components/LeadPane";
import type { Lead, Post, Thread } from "../../ui/src/api/types";
import { defFixture, fixture } from "./support/schema";
import { fakeApi } from "./support/fake";

function post(over: Partial<Post>): Post {
  return defFixture<Post>("post", over as never);
}

const OPERATOR_POST = post({
  id: "m1",
  at: new Date().toISOString(),
  from: "ada",
  author: "ada@alpha",
  to: "planner",
  text: "What blocks the preview cut?",
  operator: true,
  harness: { value: "operator", recorded: true, note: "an operator post" },
  receipts: [{ agent: "planner", words: ["posted", "inbox read", "wake confirmed"] }],
});

const LEAD_POST = post({
  id: "m2",
  at: new Date().toISOString(),
  from: "planner",
  author: "planner@alpha",
  to: "ada",
  text: "T-4 waits on T-3's accept. No owner action.",
  harness: { value: "unknown", recorded: false, note: "no harness recorded for this post" },
});

const WORKING: Partial<Lead> = {
  lead: "planner",
  needs_lead: false,
  status: {
    seat: "planner",
    harness: "codex",
    state: "working",
    detail: "run open",
    running: { ticket: "T-3", elapsed_s: 840, tokens: null },
    last_output_at: new Date(Date.now() - 40_000).toISOString(),
    last_output_source: "trajectory",
    limit: null,
    limit_until: "",
    auth: { state: "ok", label: "signed in", cmd: "" },
    usage: {
      provider: "codex",
      status: "ok",
      level: "green",
      text: "62% of the weekly window left",
      remaining_pct: 62,
      reset: "17:40",
      checked_at: new Date(Date.now() - 12 * 60_000).toISOString(),
      age: "12m",
    },
    reachable: true,
    capability: { midrun: false, line: "answers on its next turn", reason: "codex has no live socket" },
    wake_mode: "continuous",
    cannot_answer: null,
  },
};

describe("chat with the lead", () => {
  it("shows the lead, its harness and whether it takes mid-run messages", async () => {
    const { api } = fakeApi({ lead: WORKING, thread: { messages: [OPERATOR_POST, LEAD_POST] } });
    render(<App api={api} />);
    expect(await screen.findByTestId("lead-name")).toHaveTextContent("planner@alpha");
    expect(screen.getByTestId("lead-harness")).toHaveTextContent("codex");
    expect(screen.getByTestId("lead-capability")).toHaveTextContent("answers on its next turn");
  });

  it("shows liveness, run time, last output, limit, auth and usage WITH its age", async () => {
    const { api } = fakeApi({ lead: WORKING, thread: { messages: [LEAD_POST] } });
    render(<App api={api} />);
    const strip = await screen.findByTestId("lead-status");
    expect(strip).toHaveTextContent("working");
    expect(strip).toHaveTextContent("T-3");
    expect(strip).toHaveTextContent("14m");
    expect(strip).toHaveTextContent(/last output \d+s ago/);
    expect(strip).toHaveTextContent("limit none");
    expect(strip).toHaveTextContent("signed in");
    const usage = screen.getByTestId("lead-usage");
    expect(usage).toHaveTextContent("62% left");
    expect(usage).toHaveTextContent("checked 12m ago");
  });

  it("says age unknown rather than implying a fresh reading", async () => {
    const stale: Partial<Lead> = {
      ...WORKING,
      status: { ...WORKING.status!, usage: { ...WORKING.status!.usage, remaining_pct: null, checked_at: "", age: "age unknown" } },
    };
    const { api } = fakeApi({ lead: stale, thread: { messages: [LEAD_POST] } });
    render(<App api={api} />);
    const usage = await screen.findByTestId("lead-usage");
    expect(usage).toHaveTextContent("share unknown");
    expect(usage).toHaveTextContent("age unknown");
    expect(usage.textContent).not.toMatch(/\d+% left/);
  });

  it("shows a run whose tokens were never recorded as unknown", async () => {
    const { api } = fakeApi({ lead: WORKING, thread: { messages: [LEAD_POST] } });
    render(<App api={api} />);
    expect(await screen.findByTestId("lead-status")).toHaveTextContent("unknown");
  });

  it("shows each post as seat@project with a local time", async () => {
    const { api } = fakeApi({ lead: WORKING, thread: { messages: [OPERATOR_POST, LEAD_POST] } });
    render(<App api={api} />);
    const posts = await screen.findAllByTestId("post");
    expect(posts).toHaveLength(2);
    expect(posts[0]).toHaveTextContent("ada@alpha");
    expect(posts[0]).toHaveTextContent("operator");
    expect(posts[0]).toHaveTextContent(/Today at /);
    expect(posts[1]).toHaveTextContent("planner@alpha");
  });

  it("marks a harness that was never recorded as unknown, and never guesses one", async () => {
    const { api } = fakeApi({ lead: WORKING, thread: { messages: [LEAD_POST] } });
    render(<App api={api} />);
    const badge = (await screen.findAllByTestId("harness-badge"))[0];
    expect(badge).toHaveTextContent("unknown");
    expect(badge).toHaveTextContent("not recorded");
    expect(badge.textContent).not.toContain("claude");
  });

  it("labels delivery receipts as receipts and never as an acknowledgement", async () => {
    const { api } = fakeApi({ lead: WORKING, thread: { messages: [OPERATOR_POST] } });
    render(<App api={api} />);
    const receipts = await screen.findByTestId("receipts");
    expect(receipts).toHaveTextContent("delivery receipt");
    expect(receipts).toHaveTextContent("posted");
    expect(receipts).toHaveTextContent("inbox read");
    expect(receipts).toHaveTextContent("wake confirmed");
    expect(receipts.textContent?.toLowerCase()).not.toMatch(/acknowledg|\back\b|understood|on it/);
  });

  it("refuses an acknowledgement-shaped receipt word on screen", async () => {
    // The contract forbids this word, and the fixtures enforce it: neither
    // `defFixture("post")` nor `fixture("thread.json")` will carry it. It is
    // written in past validation on purpose, to prove the screen defends
    // itself too — a receipt must never be readable as the agent having
    // agreed to anything, whatever reaches the app.
    const thread = fixture<Thread>("thread.json", {
      project: "alpha",
      operator: "ada",
      lead: "planner",
      with: "planner",
      messages: [OPERATOR_POST],
    });
    thread.messages[0].receipts = [{ agent: "planner", words: ["posted", "acknowledged"] }];
    const lead = fixture<Lead>("lead.json", { project: "alpha", operator: "ada", lead: "planner", needs_lead: false, ...WORKING });
    const { api } = fakeApi();
    render(
      <LeadPane
        api={api}
        project="alpha"
        lead={lead}
        leadError={null}
        thread={thread}
        threadError={null}
        knownTickets={new Set()}
        onTicket={() => {}}
        onReload={() => {}}
      />,
    );
    const receipts = await screen.findByTestId("receipts");
    expect(receipts).toHaveTextContent("refused as not a delivery fact");
    expect(receipts).toHaveTextContent("posted");
  });

  it("copies a post", async () => {
    const user = userEvent.setup();
    const { api } = fakeApi({ lead: WORKING, thread: { messages: [LEAD_POST] } });
    render(<App api={api} />);
    const posts = await screen.findAllByTestId("post");
    await user.click(within(posts[0]).getByRole("button", { name: /^Copy:/ }));
    // user-event installs its own clipboard stub, so the assertion reads it
    // back rather than the one tests/ui/setup.ts installs for the app.
    expect(await navigator.clipboard.readText()).toBe(LEAD_POST.text);
  });

  it("pages back through history with before=", async () => {
    const user = userEvent.setup();
    const older = post({ id: "m0", from: "planner", author: "planner@alpha", text: "older than the window" });
    const { api, calls } = fakeApi({
      lead: WORKING,
      thread: { messages: [LEAD_POST], has_more: true, oldest_id: "m2" },
      pages: { m2: { messages: [older], has_more: false, oldest_id: "m0" } },
    });
    render(<App api={api} />);
    await user.click(await screen.findByRole("button", { name: /Load older messages/ }));
    await waitFor(() => expect(screen.getAllByTestId("post")).toHaveLength(2));
    expect(screen.getAllByTestId("post")[0]).toHaveTextContent("older than the window");
    expect(calls.some((c) => c.startsWith("thread:alpha:planner:m2"))).toBe(true);
  });

  it("posts through the composer as the operator", async () => {
    const user = userEvent.setup();
    const { api, posted } = fakeApi({ lead: WORKING, thread: { messages: [LEAD_POST] } });
    render(<App api={api} />);
    await user.type(await screen.findByLabelText("Tell the lead"), "status on the cut?");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ from: "ada", text: "status on the cut?", to: "planner" });
  });

  it("says a next-turn lead cannot be interrupted, under the Send button", async () => {
    const { api } = fakeApi({ lead: WORKING, thread: { messages: [LEAD_POST] } });
    render(<App api={api} />);
    expect(await screen.findByTestId("next-turn-note")).toHaveTextContent("answers on its next turn");
  });

  it("shows the exact command instead of a dead composer when no operator is configured", async () => {
    const { api } = fakeApi({
      session: { operator: "", operator_note: "set an operator: atm ui --operator <name>" },
      lead: { ...WORKING, operator: "", operator_note: "set an operator: atm ui --operator <name>" },
      thread: { messages: [LEAD_POST], operator: "", operator_note: "set an operator: atm ui --operator <name>" },
    });
    render(<App api={api} />);
    const off = await screen.findByTestId("composer-no-operator");
    expect(off).toHaveTextContent("No operator is configured");
    expect(off).toHaveTextContent("atm ui --operator <name>");
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    expect(screen.getByTestId("read-only")).toHaveTextContent("read-only");
  });

  it("asks the operator to pick a lead instead of guessing one", async () => {
    const { api } = fakeApi({
      lead: {
        lead: "",
        needs_lead: true,
        status: null,
        lead_note: "master.json.lead is unset",
        picker: [
          { seat: "planner", harness: "codex", capability: "answers on its next turn" },
          { seat: "coder", harness: "claude", capability: "takes mid-run messages" },
        ],
      },
      thread: { needs_lead: true, lead: "", with: "" },
    });
    render(<App api={api} />);
    const picker = await screen.findByTestId("lead-picker");
    expect(picker).toHaveTextContent("Pick who you talk to on this project");
    expect(picker).toHaveTextContent("planner@alpha");
    expect(picker).toHaveTextContent("answers on its next turn");
    expect(picker).toHaveTextContent("takes mid-run messages");
    expect(screen.queryByTestId("thread")).toBeNull();
    expect(screen.queryByTestId("lead-status")).toBeNull();
  });

  it("says plainly when the lead cannot answer, and keeps the post on the board", async () => {
    const limited: Partial<Lead> = {
      ...WORKING,
      status: {
        ...WORKING.status!,
        state: "limited",
        limit: { until: "17:40" },
        limit_until: "17:40",
        cannot_answer: { kind: "limited", text: "Lead is limited until 17:40.", cmd: "atm harness usage" },
      },
    };
    const { api } = fakeApi({ lead: limited, thread: { messages: [LEAD_POST] } });
    render(<App api={api} />);
    const warn = await screen.findByTestId("cannot-answer");
    expect(warn).toHaveTextContent("limited until 17:40");
    expect(warn).toHaveTextContent("Your message still lands on the board");
    expect(warn).toHaveTextContent("atm harness usage");
  });

  it("reports a failed thread read instead of an empty thread", async () => {
    const { api } = fakeApi({ lead: WORKING, fail: { thread: "no answer from the board" } });
    render(<App api={api} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The thread could not be read");
  });
});
