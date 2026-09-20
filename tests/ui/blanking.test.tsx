/**
 * The app may not blank itself.
 *
 * A blank page is the least honest failure this app could have: it looks
 * exactly like an empty board, and an operator has no way to tell the
 * difference. These tests take the shapes that used to do it — a `nodes` that
 * is not a list, a snapshot with no `agents` at all, and a throw in the shell
 * above every column boundary — and require that something readable is on
 * screen afterwards.
 *
 * The payloads here come through `fakeApi({ raw: ... })`, which bypasses the
 * schema-derived fixtures on purpose: the contract forbids these shapes, so
 * they cannot be built from it, and the app still has to survive one.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Root } from "../../ui/src/App";
import type { AtmanApi } from "../../ui/src/api/client";
import { fakeApi } from "./support/fake";
import { fixture } from "./support/schema";

async function shellIsThere() {
  expect(await screen.findByRole("navigation", { name: /Project and views/ })).toBeInTheDocument();
}

describe("a payload the contract forbids", () => {
  it("does not blank the page when plan.nodes is not a list", async () => {
    // `ticketIdsOf(plan.nodes)` ran above all three column boundaries, so this
    // threw "e.map is not a function" straight through them and left #root empty.
    // project must be the one the shell is on, or the (correct) project guard
    // rejects the payload before its shape ever matters.
    const plan = { ...fixture<Record<string, unknown>>("plan.json"), project: "alpha", available: true, nodes: "not a list" };
    const { api } = fakeApi({ raw: { plan } });
    render(<Root api={api} />);
    await shellIsThere();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBeTruthy();
    expect(alert).toHaveTextContent(/not the shape the contract describes/);
    // The chat is a separate read and must still be readable.
    expect(await screen.findByLabelText("Chat with the lead")).toBeInTheDocument();
    expect(document.body.textContent?.length || 0).toBeGreaterThan(200);
  });

  it("does not blank the page when the snapshot carries no agents", async () => {
    // The sidebar read `board.agents.length` for its seat count, above the
    // boundaries, and threw on a null.
    const board = { ...fixture<Record<string, unknown>>("board.json"), project: "alpha", agents: null };
    const { api } = fakeApi({ raw: { board } });
    render(<Root api={api} />);
    await shellIsThere();
    expect(await screen.findByLabelText("Chat with the lead")).toBeInTheDocument();
    // The Fleet view says the list is unreadable rather than "no seats".
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /^Fleet/ }));
    const said = (await screen.findByLabelText("Fleet")).textContent || "";
    expect(said).toMatch(/seat list unreadable|carries no seat list/);
    expect(said).not.toMatch(/no seats registered/);
  });

  it("does not blank the page when agent_map.groups is not a list", async () => {
    const board = { ...fixture<Record<string, unknown>>("board.json"), project: "alpha", agent_map: { groups: 7 } };
    const { api } = fakeApi({ raw: { board } });
    render(<Root api={api} />);
    await shellIsThere();
    expect(await screen.findByLabelText("Chat with the lead")).toBeInTheDocument();
  });
});

describe("the top-level boundary", () => {
  it("catches a throw in the shell, which no column boundary can", async () => {
    // Reading api.origin happens in App's own body, above every column. Before
    // Root existed this left #root at 0 characters with a console line.
    const thrower = {
      get origin(): never {
        throw new Error("the shell could not work out where the API is");
      },
    } as unknown as AtmanApi;
    render(<Root api={thrower} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The app could not be drawn");
    expect(alert).toHaveTextContent("the shell could not work out where the API is");
    expect(screen.getByText(/not a statement about the board/)).toBeInTheDocument();
  });

  it("is what main.tsx mounts — App bare has no floor under it", async () => {
    // The old version of this test asserted `typeof App === "function"` and so
    // could not fail. This one mounts the real entry point with the App module
    // replaced by two markers, and fails if main.tsx ever renders App directly.
    vi.resetModules();
    vi.doMock("../../ui/src/App", () => ({
      Root: () => <p>root, with the floor under it</p>,
      App: () => <p>app, bare</p>,
    }));
    document.body.innerHTML = '<div id="root"></div>';
    await import("../../ui/src/main");
    await waitFor(() => expect(document.body.textContent).toContain("root, with the floor under it"));
    expect(document.body.textContent).not.toContain("app, bare");
    vi.doUnmock("../../ui/src/App");
    vi.resetModules();
  });
});
