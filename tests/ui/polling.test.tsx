/**
 * The polling and freshness path.
 *
 * Every screen is a repeated read, so what happens on the *second* read
 * matters as much as the first: a refresh that fails must not wipe the data
 * already on screen, and the page must not keep presenting it as current.
 * Both were untested.
 */

import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "../../ui/src/App";
import { useResource } from "../../ui/src/state/useResource";
import type { PlanNode } from "../../ui/src/api/types";
import { fakeApi } from "./support/fake";
import { subFixture } from "./support/schema";

describe("useResource", () => {
  it("records when a value was read, for the freshness line", async () => {
    const { result } = renderHook(() => useResource(() => Promise.resolve("first"), []));
    await waitFor(() => expect(result.current.data).toBe("first"));
    expect(result.current.readAt).toBeInstanceOf(Date);
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("keeps the last good value when a refresh fails, and reports the failure", async () => {
    let call = 0;
    const load = () => {
      call += 1;
      return call === 1 ? Promise.resolve("good") : Promise.reject(new Error("board went away"));
    };
    const { result } = renderHook(() => useResource(load, []));
    await waitFor(() => expect(result.current.data).toBe("good"));
    const firstRead = result.current.readAt;

    act(() => result.current.reload());
    await waitFor(() => expect(result.current.error).toBeTruthy());
    // The data an operator is looking at is still there — blanking it would
    // look like an empty board — but it is not presented as newly read.
    expect(result.current.data).toBe("good");
    expect((result.current.error as Error).message).toBe("board went away");
    expect(result.current.readAt).toBe(firstRead);
  });

  it("clears an old failure once a read works again", async () => {
    let call = 0;
    const load = () => {
      call += 1;
      return call === 1 ? Promise.reject(new Error("first read failed")) : Promise.resolve("recovered");
    };
    const { result } = renderHook(() => useResource(load, []));
    await waitFor(() => expect(result.current.error).toBeTruthy());
    expect(result.current.data).toBeNull();

    act(() => result.current.reload());
    await waitFor(() => expect(result.current.data).toBe("recovered"));
    expect(result.current.error).toBeNull();
  });
});

describe("the freshness line", () => {
  it("says when the board was read", async () => {
    const { api } = fakeApi();
    render(<App api={api} />);
    await waitFor(() => expect(screen.getByTestId("freshness")).toHaveTextContent(/board read \d+s ago/));
  });

  it("says the last read failed instead of implying the screen is current", async () => {
    const { api } = fakeApi({ fail: { board: "no answer from http://127.0.0.1:8765" } });
    render(<App api={api} />);
    await waitFor(() => expect(screen.getByTestId("freshness")).toHaveTextContent("last read failed: board"));
    // The chat and the plan are separate reads and are unaffected.
    expect(screen.getByLabelText("Chat with the lead")).toBeInTheDocument();
    expect(await screen.findByLabelText("Execution plan")).toBeInTheDocument();
    // A view with no data at all says so, rather than looking like an empty board.
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /^Fleet/ }));
    expect(await screen.findByText(/The fleet could not be read/)).toBeInTheDocument();
  });

  it("keeps the seats on screen when a later poll of the board fails", async () => {
    // A same-deps refresh: the poll timer fires, the read fails, and what the
    // operator is reading stays put while the freshness line says so. This is
    // the behaviour the project-switch fix must NOT take away.
    vi.useFakeTimers();
    try {
      const { api, calls } = fakeApi({
        board: {
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
          ],
        },
        failAfterFirst: { board: "the board stopped answering" },
      });
      render(<App api={api} />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });
      // #fleet, set directly: userEvent and fake timers do not mix well here.
      window.location.hash = "fleet";
      await act(async () => {
        window.dispatchEvent(new HashChangeEvent("hashchange"));
        await vi.advanceTimersByTimeAsync(50);
      });
      expect(screen.getByTestId("seats")).toHaveTextContent("planner@alpha");

      // Now let the poll come round and fail.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(9000);
      });
      expect(calls.filter((c) => c === "board:alpha").length).toBeGreaterThan(1);
      expect(screen.getByTestId("seats")).toHaveTextContent("planner@alpha");
      expect(screen.getByTestId("freshness")).toHaveTextContent("last read failed: board");
      expect(screen.getByLabelText("Fleet").textContent).not.toMatch(/could not be read/);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not keep one project's records after a switch, even when the new read fails", async () => {
    // The regression this pairs with: stale data surviving a deps change was
    // rendered against the NEW project's slug, so one board's steps appeared as
    // the other board's work. A failed read after a switch must be an error.
    const user = userEvent.setup();
    const { api } = fakeApi({
      plan: {
        available: true,
        objective: { text: "Alpha objective", exit_criterion: "alpha ships", state: "active" },
        nodes: [
          subFixture<PlanNode>("plan.json", "/properties/nodes/items", {
            id: "T-1",
            title: "Alpha's own step",
            status: "done",
            status_label: "done, accepted",
            accepted: true,
            released: true,
            unverified: false,
            owner: "coder",
          } as never),
        ],
        layers: [["T-1"]],
        order: ["T-1"],
      },
      failByProject: { plan: { demo: "no answer from the board" } },
    });
    render(<App api={api} />);
    expect(await screen.findByText("Alpha's own step")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "demo" }));

    expect(await screen.findByText(/The plan could not be read/)).toBeInTheDocument();
    // Not one trace of alpha's records, and nothing labelled @demo from them.
    expect(screen.queryByText("Alpha's own step")).toBeNull();
    const body = document.body.textContent || "";
    expect(body).not.toContain("Alpha objective");
    expect(body).not.toContain("coder@demo");
    expect(body).not.toContain("done, accepted");
  });

  it("does not keep one project's seat count after a switch whose read fails", async () => {
    const user = userEvent.setup();
    const seat = (name: string) => ({
      name,
      state: "UP",
      harness: "codex",
      lifecycle: "persistent",
      reachable: true,
      adapter_state: "ready",
      wake_mode: "continuous",
      limit: null,
      limit_until: "",
      auth_surface: { state: "ok", label: "signed in" },
    });
    const { api } = fakeApi({
      board: { agents: [seat("planner"), seat("coder"), seat("rev"), seat("scout"), seat("dave"), seat("nora"), seat("ada")] },
      failByProject: { board: { demo: "no answer from the board" } },
    });
    render(<App api={api} />);
    // Seven seats on alpha.
    expect(await screen.findByRole("button", { name: /^Fleet 7/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "demo" }));

    // The count must go away, not be re-attributed to demo.
    await waitFor(() => expect(screen.getByRole("button", { name: /^Fleet$/ })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /^Fleet$/ }));
    expect(await screen.findByText(/The fleet could not be read/)).toBeInTheDocument();
    const body = document.body.textContent || "";
    expect(body).not.toContain("nora@demo");
    expect(body).not.toContain("7 seats");
  });

  it("does not leave the previous ticket's evidence open when a ticket read fails", async () => {
    const user = userEvent.setup();
    const { api } = fakeApi({
      plan: {
        available: true,
        nodes: [
          subFixture<PlanNode>("plan.json", "/properties/nodes/items", { id: "T-3", title: "In flight" } as never),
          subFixture<PlanNode>("plan.json", "/properties/nodes/items", { id: "T-5", title: "Waits on a dep" } as never),
        ],
        layers: [["T-3", "T-5"]],
        order: ["T-3", "T-5"],
      },
      tickets: { "T-3": { id: "T-3", title: "In flight", status: "claimed", status_label: "in flight" } },
    });
    render(<App api={api} />);
    await user.click(await screen.findByRole("button", { name: /T-3/ }));
    expect(await screen.findByTestId("ticket-pane")).toHaveTextContent("In flight");

    // T-5 has no entry in `tickets`, so the fake refuses it the way the server
    // refuses an unknown id.
    await user.click(screen.getByRole("button", { name: /T-5/ }));

    expect(await screen.findByText(/The ticket could not be read/)).toBeInTheDocument();
    expect(screen.queryByTestId("ticket-pane")).toBeNull();
    // T-3's evidence must be gone: its review, runs and accept belong to T-3.
    expect(document.body.textContent).not.toContain("in flight");
    expect(screen.getByTestId("freshness")).toHaveTextContent("last read failed: ticket");
  });
});
