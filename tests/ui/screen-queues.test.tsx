/**
 * Needs you, Fleet, Runs and the shell, mounted.
 *
 * Needs you is read-only and unstructured: prose that says "ruling" is still
 * an ask. Fleet and Runs read the board snapshot, where a seat's harness may
 * be `unknown` and a run's tokens may never have been recorded.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App } from "../../ui/src/App";
import { Boundary } from "../../ui/src/components/Boundary";
import type { Board, NeedsYou, NeedsYouItem } from "../../ui/src/api/types";
import { fakeApi } from "./support/fake";
import { subFixture } from "./support/schema";

function ask(over: Partial<NeedsYouItem>): NeedsYouItem {
  return subFixture<NeedsYouItem>("needs-you.json", "/properties/items/items", over as never);
}

const ASKS: Partial<NeedsYou> = {
  count: 2,
  operator: "ada",
  note: "unstructured asks only; a ruling needs a record",
  items: [
    ask({
      kind: "message",
      why: "prose DECIDE",
      id: "m4",
      at: "2026-09-20T09:40:00Z",
      from: "planner",
      author: "planner@alpha",
      re: "T-3",
      text: "DECIDE: rule A or B? ruling: A",
    }),
    ask({
      kind: "message",
      why: "stuck for more than an hour",
      id: "m5",
      at: "2026-09-20T08:10:00Z",
      from: "scout",
      author: "scout@alpha",
      text: "stuck: staging host unreachable",
    }),
  ],
};

const BOARD: Partial<Board> = {
  agents: [
    {
      name: "planner",
      state: "UP",
      harness: "codex",
      lifecycle: "persistent",
      reachable: true,
      adapter_state: "ready",
      wake_mode: "continuous",
      limit: null,
      limit_until: "",
      auth_surface: { state: "ok", label: "signed in" },
    },
    {
      name: "rev",
      state: "UP",
      harness: "unknown",
      lifecycle: "ephemeral",
      reachable: true,
      adapter_state: "ready",
      wake_mode: "poll",
      limit: { until: "17:40" },
      limit_until: "17:40",
      auth_surface: { state: "login_required", label: "logged out", recovery: { cmd: "atm auth reconnect rev" } },
    },
  ],
  provider_usage: [{ provider: "codex", level: "green", remaining_pct: 62, text: "62% of the weekly window left" }],
  agent_map: {
    groups: [
      {
        ticket: "T-3",
        title: "In flight",
        status: "claimed",
        running: 1,
        runs: 2,
        elapsed_s: 1020,
        tokens: 41200,
        tokens_unknown: 1,
        rows: [
          { seat: "coder", harness: "unknown", ticket: "T-3", role: "author", state: "running", elapsed_s: 840, tokens: null, tokens_in: null, tokens_out: null, started: "2026-09-20T09:46:00Z" },
          // The live snapshot records a run's verdict as {kind, sha}, not as a
          // string: board.json leaves these rows informational, and rendering
          // the object raw took the whole app down before verdictChip existed.
          { seat: "rev", harness: "codex", ticket: "T-3", role: "reviewer", state: "done", verdict: { kind: "reject", sha: "9c1e5a0b7d3f2e1a4b6c8d0e2f4a6b8c0d1e3f50" }, elapsed_s: 180, tokens: 41200, tokens_in: 30000, tokens_out: 11200, started: "2026-09-20T09:51:00Z" },
        ],
      },
    ],
  },
};

describe("needs you", () => {
  it("shows every item as an unstructured ask, never as a ruling", async () => {
    const user = userEvent.setup();
    const { api } = fakeApi({ needsYou: ASKS });
    render(<App api={api} />);
    await user.click(await screen.findByRole("button", { name: /Needs you/ }));
    const asks = await screen.findByTestId("asks");
    expect(within(asks).getAllByText("asked (unstructured)")).toHaveLength(2);
    expect(asks).toHaveTextContent("DECIDE: rule A or B? ruling: A");
    // The prose contains the word "ruling"; the state beside it is still asked.
    expect(asks.textContent).not.toMatch(/\bruled\b/);
    expect(asks).toHaveTextContent("planner@alpha");
    expect(asks).toHaveTextContent("prose DECIDE");
  });

  it("is read-only and says so", async () => {
    const user = userEvent.setup();
    const { api } = fakeApi({ needsYou: ASKS });
    render(<App api={api} />);
    await user.click(await screen.findByRole("button", { name: /Needs you/ }));
    expect(await screen.findByText(/Read-only/)).toBeInTheDocument();
    const pane = screen.getByLabelText("Needs you");
    // The only controls are the ticket links; nothing here writes.
    for (const button of within(pane).queryAllByRole("button")) {
      expect(button.className).toContain("link-ticket");
    }
  });

  it("carries the count into the sidebar", async () => {
    const { api } = fakeApi({ needsYou: ASKS });
    render(<App api={api} />);
    expect(await screen.findByRole("button", { name: /Needs you 2/ })).toBeInTheDocument();
  });

  it("says nothing is waiting rather than showing a blank screen", async () => {
    const user = userEvent.setup();
    const { api } = fakeApi({ needsYou: { count: 0, items: [], note: "" } });
    render(<App api={api} />);
    await user.click(await screen.findByRole("button", { name: /Needs you/ }));
    expect(await screen.findByText(/Nothing is waiting on you/)).toBeInTheDocument();
  });
});

describe("fleet", () => {
  it("shows each seat as seat@project with its state, harness, limit and auth", async () => {
    const user = userEvent.setup();
    const { api } = fakeApi({ board: BOARD });
    render(<App api={api} />);
    await user.click(await screen.findByRole("button", { name: /Fleet/ }));
    const seats = await screen.findByTestId("seats");
    expect(seats).toHaveTextContent("planner@alpha");
    expect(seats).toHaveTextContent("codex");
    expect(seats).toHaveTextContent("persistent");
    expect(seats).toHaveTextContent("wake continuous");
    expect(seats).toHaveTextContent("rev@alpha");
    expect(seats).toHaveTextContent("limited until 17:40");
    expect(seats).toHaveTextContent("logged out");
    expect(seats).toHaveTextContent("atm auth reconnect rev");
  });

  it("shows a harness value the route gives it, whatever it is", async () => {
    const user = userEvent.setup();
    const { api } = fakeApi({ board: BOARD });
    render(<App api={api} />);
    await user.click(await screen.findByRole("button", { name: /Fleet/ }));
    const seats = await screen.findByTestId("seats");
    // Since #267 the route reports "unknown" for a seat with no workforce
    // entry, which is what BOARD's second seat carries and what the live
    // server now returns — verified against a real board, not assumed.
    expect(within(seats).getAllByText("unknown").length).toBeGreaterThan(0);
  });

  it("says the harness column is the seat's value now, not a stamp on a run or a post", async () => {
    const user = userEvent.setup();
    // Two seats with no workforce entry, as the route reports them since #267.
    const unrecorded = {
      ...BOARD,
      agents: [
        { ...BOARD.agents![0], name: "nohar", harness: "unknown" },
        { ...BOARD.agents![0], name: "ada", harness: "unknown" },
      ],
    };
    const { api } = fakeApi({ board: unrecorded });
    render(<App api={api} />);
    await user.click(await screen.findByRole("button", { name: /Fleet/ }));
    const caveat = await screen.findByTestId("fleet-harness-caveat");
    expect(caveat).toHaveTextContent(/value/);
    expect(caveat).toHaveTextContent(/not a stamp on a run or a post/);
    expect(caveat).toHaveTextContent(/read the badge on that post in the chat/);
    // The caveat that named #267 as a pending fix is no longer true: it merged.
    expect(caveat.textContent).not.toContain("#267 is the fix");
    expect(caveat.textContent).not.toMatch(/still fills it in|until it lands/);
    const pane = screen.getByLabelText("Fleet").textContent || "";
    expect(pane).toMatch(/nohar@alpha/);
    expect(pane).toMatch(/unknown/);
  });

  it("shows a provider reading from the snapshot as having no age", async () => {
    const user = userEvent.setup();
    const { api } = fakeApi({ board: BOARD });
    render(<App api={api} />);
    await user.click(await screen.findByRole("button", { name: /Fleet/ }));
    const usage = await screen.findByTestId("fleet-usage");
    expect(usage).toHaveTextContent("62% left");
    expect(usage).toHaveTextContent("age unknown");
  });
});

describe("runs", () => {
  it("groups runs by ticket with elapsed time and tokens", async () => {
    const user = userEvent.setup();
    const { api } = fakeApi({ board: BOARD });
    render(<App api={api} />);
    await user.click(await screen.findByRole("button", { name: /Runs/ }));
    const group = await screen.findByTestId("run-group");
    expect(group).toHaveTextContent("T-3");
    expect(group).toHaveTextContent("1 running");
    expect(group).toHaveTextContent("17m");
    expect(group).toHaveTextContent("41,200 tokens");
    expect(group).toHaveTextContent("1 run(s) with no token record");
    expect(group).toHaveTextContent("coder@alpha");
    expect(group).toHaveTextContent("unknown");
    expect(group).toHaveTextContent("reject");
  });

  it("renders a verdict recorded as an object instead of blanking the screen", async () => {
    const user = userEvent.setup();
    const { api } = fakeApi({ board: BOARD });
    render(<App api={api} />);
    await user.click(await screen.findByRole("button", { name: /Runs/ }));
    const group = await screen.findByTestId("run-group");
    expect(group).toHaveTextContent("reject");
    // The sha stays whole, in the title, never shortened on screen.
    expect(within(group).getByTitle(/recorded at 9c1e5a0b7d3f2e1a4b6c8d0e2f4a6b8c0d1e3f50/)).toBeInTheDocument();
    expect(screen.getByLabelText("Runs")).toBeInTheDocument();
  });

  it("names the pane that could not be drawn instead of blanking the app", () => {
    // The boundary is here because a blank page is the least honest failure
    // this app could have: it looks exactly like an empty board.
    function Explodes(): never {
      throw new Error("verdict is not a string");
    }
    render(
      <Boundary what="This view">
        <Explodes />
      </Boundary>,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("This view could not be drawn");
    expect(alert).toHaveTextContent("verdict is not a string");
    expect(screen.getByText(/not a statement about the board/)).toBeInTheDocument();
  });

  it("opens the drill-down from a run group", async () => {
    const user = userEvent.setup();
    const { api } = fakeApi({ board: BOARD, tickets: { "T-3": { id: "T-3", title: "In flight" } } });
    render(<App api={api} />);
    await user.click(await screen.findByRole("button", { name: /Runs/ }));
    await user.click(await screen.findByRole("button", { name: "T-3" }));
    expect(await screen.findByTestId("ticket-pane")).toHaveTextContent("In flight");
  });

  it("says a board with no run map has none", async () => {
    const user = userEvent.setup();
    const { api } = fakeApi({ board: { agent_map: null } });
    render(<App api={api} />);
    await user.click(await screen.findByRole("button", { name: /Runs/ }));
    expect(await screen.findByText(/no run map/)).toBeInTheDocument();
  });
});

describe("the shell", () => {
  it("lists every project with its open count and lead, and switches the board", async () => {
    const user = userEvent.setup();
    const { api, calls } = fakeApi({ board: BOARD });
    render(<App api={api} />);
    await screen.findByTestId("project-switch");
    expect(screen.getByRole("button", { name: "alpha" })).toBeInTheDocument();
    expect(screen.getByText("12 open")).toBeInTheDocument();
    expect(screen.getByText("lead planner")).toBeInTheDocument();
    expect(screen.getByText("no lead picked")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "demo" }));
    await waitFor(() => expect(calls.some((c) => c === "board:demo")).toBe(true));
    expect(calls.some((c) => c === "plan:demo")).toBe(true);
    expect(calls.some((c) => c === "lead:demo")).toBe(true);
    expect(calls.some((c) => c === "needs-you:demo")).toBe(true);
  });

  it("keeps chat and the plan on screen together", async () => {
    const { api } = fakeApi({ board: BOARD });
    render(<App api={api} />);
    expect(await screen.findByLabelText("Chat with the lead")).toBeInTheDocument();
    expect(await screen.findByLabelText("Execution plan")).toBeInTheDocument();
  });

  it("offers every phase-1 view from the keyboard", async () => {
    const { api } = fakeApi();
    render(<App api={api} />);
    for (const name of ["Lead", "Plan", "Needs you", "Fleet", "Runs"]) {
      const button = await screen.findByRole("button", { name: new RegExp(`^${name}`) });
      expect(button.tagName).toBe("BUTTON");
      expect(button).not.toBeDisabled();
    }
  });

  it("refuses an API origin that is not loopback instead of calling it", async () => {
    const { api } = fakeApi({ origin: { api: "", server: "", sameOrigin: false, refused: "http://example.test is not a loopback origin" } });
    render(<App api={api} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("not a loopback origin");
  });

  it("draws nothing but the failure when the session cannot be read", async () => {
    const { api } = fakeApi({ fail: { session: "no answer from http://127.0.0.1:8765" } });
    render(<App api={api} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The session could not be read");
    expect(screen.queryByLabelText("Execution plan")).toBeNull();
  });
});
