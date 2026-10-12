/**
 * The app may not blank itself.
 *
 * A blank page is the least honest failure this app could have: it looks
 * exactly like an empty board, and an operator has no way to tell the
 * difference. These tests take the shapes that used to do it — a `nodes` that
 * is not a list, a snapshot with no `agents` at all, and a throw in the shell
 * above every boundary — and require that something readable is on screen.
 *
 * The payloads here come through `fakeApi({ raw: ... })`, which bypasses the
 * schema-derived fixtures on purpose: the contract forbids these shapes, so
 * they cannot be built from it, and the app still has to survive one.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Root } from "../../ui/src/App";
import type { AtmanApi } from "../../ui/src/api/client";
import { fakeApi } from "./support/fake";
import { fixture } from "./support/schema";
import { STATES } from "./support/v3";

async function shellIsThere() {
  expect(await screen.findByTestId("you")).toBeInTheDocument();
  expect(await screen.findByTestId("talk")).toBeInTheDocument();
}

describe("a payload the contract forbids", () => {
  it("does not blank the page when plan.nodes is not a list", async () => {
    const plan = { ...fixture<Record<string, unknown>>("plan.json"), project: "alpha", available: true, nodes: "not a list" };
    const { api } = fakeApi({ ...STATES.fresh, raw: { plan } });
    render(<Root api={api} />);
    await shellIsThere();
    // The sections are drawn from the plan; with no list there are no cards, and the strip still reads.
    expect(await screen.findByTestId("strip")).toBeInTheDocument();
    expect(screen.queryAllByTestId("card")).toHaveLength(0);
    expect(document.body.textContent?.length || 0).toBeGreaterThan(200);
  });

  it("does not blank the page when the snapshot carries no agents", async () => {
    const board = { ...fixture<Record<string, unknown>>("board.json"), project: "alpha", agents: null, app: { project: "alpha", operator: "ada", operator_note: "", lead: "", lead_note: "" } };
    const { api } = fakeApi({ ...STATES.fresh, raw: { board } });
    render(<Root api={api} />);
    await shellIsThere();
    expect(await screen.findByTestId("presence")).toHaveTextContent("seat list unreadable");
    expect(screen.getByTestId("presence")).not.toHaveTextContent(/no seats registered/i);
    expect(await screen.findByTestId("section-working")).toBeInTheDocument();
  });

  it("does not blank the page when agent_map.groups is not a list", async () => {
    const board = { ...fixture<Record<string, unknown>>("board.json"), project: "alpha", agent_map: { groups: 7 }, app: { project: "alpha", operator: "ada", operator_note: "", lead: "", lead_note: "" } };
    const { api } = fakeApi({ ...STATES.fresh, raw: { board } });
    render(<Root api={api} />);
    await shellIsThere();
    expect(await screen.findByTestId("section-working")).toBeInTheDocument();
  });

  it("does not blank the page when the board messages are not a list", async () => {
    const board = { ...fixture<Record<string, unknown>>("board.json"), project: "alpha", messages: "nope", app: { project: "alpha", operator: "ada", operator_note: "", lead: "", lead_note: "" } };
    const { api } = fakeApi({ ...STATES.fresh, raw: { board } });
    render(<Root api={api} />);
    await shellIsThere();
    expect(await screen.findByTestId("thread")).toHaveTextContent("No messages on the board yet.");
  });
});

describe("the top-level boundary", () => {
  it("catches a throw in the shell, which no column boundary can", async () => {
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
