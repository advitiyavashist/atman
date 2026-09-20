/**
 * The drill-down, mounted.
 *
 * This is where an operator decides whether to trust a piece of work, so the
 * assertions are about evidence: the whole review head, which verdict still
 * binds to it, tokens that were never recorded staying `unknown`, usage
 * carrying its age, and a diff offered as a command rather than a diff the
 * read route may not produce.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App } from "../../ui/src/App";
import type { Plan, PlanNode, Post, Ticket, UsageView } from "../../ui/src/api/types";
import { fakeApi } from "./support/fake";
import { defFixture, subFixture } from "./support/schema";

const HEAD = "9c1e5a0b7d3f2e1a4b6c8d0e2f4a6b8c0d1e3f50";
const OLD_HEAD = "24fb8c0aa1b2c3d4e5f60718293a4b5c6d7e8f90";

const NODE: PlanNode = subFixture<PlanNode>("plan.json", "/properties/nodes/items", {
  id: "T-3",
  title: "In flight",
  status: "claimed",
  status_label: "in flight",
  phase: "working",
  owner: "coder",
} as never);

const PLAN: Partial<Plan> = { available: true, nodes: [NODE], layers: [["T-3"]], order: ["T-3"] };

const TICKET: Partial<Ticket> = {
  id: "T-3",
  title: "In flight",
  status: "claimed",
  status_label: "in flight",
  accepted: false,
  released: false,
  owner: "coder",
  owner_at_project: "coder@alpha",
  deps: [
    { id: "T-1", state: "accepted", title: "Accepted base" },
    { id: "T-2", state: "done, not accepted", title: "Done but never accepted" },
  ],
  acceptance: { proof: "the 390px sheet opens and closes" },
  review: {
    head: HEAD,
    head_len: 40,
    label: "rejected at this head",
    verified: false,
    verdicts: [
      { kind: "reject", by: "rev", at: "2026-09-20T10:00:00Z", sha: HEAD, superseded: false, applies: true, notes: "edges overlap" },
      { kind: "accept", by: "rev", at: "2026-09-19T10:00:00Z", sha: OLD_HEAD, superseded: true, applies: false, notes: "at the old head" },
    ],
  },
  runs: [
    {
      seat: "coder",
      author: "coder@alpha",
      harness: "unknown",
      role: "author",
      state: "running",
      verdict: "",
      started: "2026-09-20T09:46:00Z",
      ended: "",
      elapsed_s: 840,
      tokens: null,
      tokens_in: null,
      tokens_out: null,
      tokens_label: "unknown",
    },
    {
      seat: "rev",
      author: "rev@alpha",
      harness: "codex",
      role: "reviewer",
      state: "done",
      verdict: "reject",
      started: "2026-09-20T09:51:00Z",
      ended: "2026-09-20T09:54:00Z",
      elapsed_s: 180,
      tokens: 41200,
      tokens_in: 30000,
      tokens_out: 11200,
      tokens_label: "41,200",
    },
  ],
  usage: [
    defFixture<UsageView>("usage_view", { provider: "claude" }),
    defFixture<UsageView>("usage_view", {
      provider: "codex",
      remaining_pct: 62,
      checked_at: new Date(Date.now() - 3 * 60_000).toISOString(),
      age: "3m",
      reset: "17:40",
    }),
  ],
  handoff: [{ from: "T-1", by: "coder", at: "2026-09-20T08:00:00Z", text: "schema frozen at the accepted head" }],
  messages: [
    defFixture<Post>("post", {
      id: "m3",
      at: "2026-09-20T09:30:00Z",
      from: "coder",
      author: "coder@alpha",
      to: "planner",
      re: "T-3",
      text: "T-3 submitted for review",
      harness: { value: "unknown", recorded: false, note: "no harness recorded for this post" },
      receipts: [{ agent: "planner", words: ["posted", "inbox read"] }],
    }),
  ],
  messages_total: 12,
  steers: [{ id: "ste-1", kind: "ask", from: "ada", seat: "coder", text: "is 390px covered?", at: "2026-09-20T09:51:00Z", receipt: "delivered-unconfirmed" }],
  artifact: { commit: "t3-branch@" + HEAD, branch: "t3-branch", pr: "pr/7", sha: HEAD },
  diff_cmd: `git diff main...${HEAD}`,
  diff_note: "a read route runs no git, so the diff is a command you run",
};

async function open(over: Partial<Ticket> = TICKET) {
  const user = userEvent.setup();
  const { api } = fakeApi({ plan: PLAN, tickets: { "T-3": over } });
  render(<App api={api} />);
  await waitFor(() => expect(screen.getAllByTestId("plan-step")).toHaveLength(1));
  await user.click(screen.getByRole("button", { name: /T-3/ }));
  return { user, pane: await screen.findByTestId("ticket-pane") };
}

describe("the drill-down", () => {
  it("shows the ticket, its owner as seat@project and its acceptance proof", async () => {
    const { pane } = await open();
    expect(pane).toHaveTextContent("T-3");
    expect(pane).toHaveTextContent("coder@alpha");
    expect(pane).toHaveTextContent("the 390px sheet opens and closes");
    expect(pane).toHaveTextContent("in flight");
  });

  it("shows each dependency's accept state, and never calls an unaccepted dep accepted", async () => {
    const { pane } = await open();
    const deps = await screen.findByTestId("deps");
    expect(deps).toHaveTextContent("accepted");
    expect(deps).toHaveTextContent("done, not accepted");
    void pane;
  });

  it("shows runs with their harness, role, elapsed time and tokens", async () => {
    await open();
    const runs = await screen.findByTestId("runs");
    expect(runs).toHaveTextContent("coder@alpha");
    expect(runs).toHaveTextContent("author");
    expect(runs).toHaveTextContent("running 14m");
    expect(runs).toHaveTextContent("rev@alpha");
    expect(runs).toHaveTextContent("reviewer");
    expect(runs).toHaveTextContent("41,200 tokens");
    expect(runs).toHaveTextContent("30,000 in · 11,200 out");
  });

  it("says unknown for a run whose tokens were never recorded, never 0", async () => {
    await open();
    const tokens = await screen.findAllByTestId("run-tokens");
    expect(tokens[0]).toHaveTextContent("unknown");
    expect(tokens[0].textContent).not.toContain("0");
  });

  it("shows a harness the board never recorded as unknown", async () => {
    await open();
    const runs = await screen.findByTestId("runs");
    expect(runs).toHaveTextContent("unknown");
    expect(runs.textContent).not.toContain("claude ·author");
  });

  it("shows each usage reading with its age, and no percentage when it has none", async () => {
    await open();
    const usage = await screen.findByTestId("usage");
    expect(usage).toHaveTextContent("share unknown");
    expect(usage).toHaveTextContent("age unknown");
    expect(usage).toHaveTextContent("62% left");
    expect(usage).toHaveTextContent("checked 3m ago");
  });

  it("shows the full 40-character review head", async () => {
    await open();
    const head = await screen.findByTestId("review-head");
    expect(head).toHaveTextContent(HEAD);
    expect(head.textContent).toHaveLength(40);
    expect(head.textContent).not.toContain("…");
  });

  it("marks a verdict that no longer binds to the current head", async () => {
    await open();
    const verdicts = await screen.findByTestId("verdicts");
    expect(verdicts).toHaveTextContent("reject");
    expect(verdicts).toHaveTextContent("accept");
    expect(verdicts).toHaveTextContent("superseded");
    expect(verdicts).toHaveTextContent("not bound to the current review head");
  });

  it("shows the handoff, the messages about it and the steers with their receipts", async () => {
    await open();
    expect(await screen.findByTestId("handoff")).toHaveTextContent("schema frozen at the accepted head");
    const msgs = screen.getByTestId("ticket-messages");
    expect(msgs).toHaveTextContent("coder@alpha");
    expect(msgs).toHaveTextContent("T-3 submitted for review");
    expect(msgs).toHaveTextContent("delivery receipt");
    expect(screen.getByText("12 total")).toBeInTheDocument();
    const steers = screen.getByTestId("steers");
    expect(steers).toHaveTextContent("is 390px covered?");
    expect(steers).toHaveTextContent("delivered-unconfirmed");
  });

  it("shows the commit, branch and PR, and the copyable diff command", async () => {
    const { user } = await open();
    const artifact = await screen.findByTestId("artifact");
    expect(artifact).toHaveTextContent("t3-branch");
    expect(artifact).toHaveTextContent(HEAD);
    expect(artifact).toHaveTextContent("pr/7");
    expect(screen.getByText(`git diff main...${HEAD}`)).toBeInTheDocument();
    expect(screen.getByText(/a read route runs no git/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: `Copy: git diff main...${HEAD}` }));
    expect(await navigator.clipboard.readText()).toBe(`git diff main...${HEAD}`);
  });

  it("says what is missing on a bare ticket instead of leaving it blank", async () => {
    await open({ id: "T-3", title: "Bare", status: "open", status_label: "open" });
    const pane = await screen.findByTestId("ticket-pane");
    expect(pane).toHaveTextContent("no runs recorded for this ticket");
    expect(pane).toHaveTextContent("no structured verdict on this ticket");
    expect(pane).toHaveTextContent("no handoff note");
    expect(pane).toHaveTextContent("no steers on this ticket");
    expect(pane).toHaveTextContent("no review head recorded");
    expect(pane.textContent).not.toContain("no review head not recorded");
  });

  it("reports a ticket that could not be read", async () => {
    const user = userEvent.setup();
    const { api } = fakeApi({ plan: PLAN, fail: { ticket: "no such ticket T-3" } });
    render(<App api={api} />);
    await waitFor(() => expect(screen.getAllByTestId("plan-step")).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: /T-3/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The ticket could not be read");
  });
});
