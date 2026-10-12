/**
 * The polling and freshness path.
 *
 * Every screen is a repeated read, so what happens on the *second* read
 * matters as much as the first: a refresh that fails must not wipe the data
 * already on screen, and the page must not keep presenting it as current.
 * And a value is only ever shown for the project it was read for.
 */

import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "../../ui/src/App";
import { useResource } from "../../ui/src/state/useResource";
import { fakeApi } from "./support/fake";
import { STATES, seat } from "./support/v3";

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
    const { api } = fakeApi(STATES.fresh);
    render(<App api={api} />);
    await waitFor(() => expect(screen.getByTestId("freshness")).toHaveTextContent(/Board read \d+s ago/));
  });

  it("says the last read failed instead of implying the screen is current, and the other reads still draw", async () => {
    const { api } = fakeApi({ ...STATES.fresh, fail: { board: "no answer from http://127.0.0.1:8765" } });
    render(<App api={api} />);
    await waitFor(() => expect(screen.getByTestId("freshness")).toHaveTextContent("last read failed: board"));
    // The plan is a separate read: the sections are up, with the seat list marked unreadable.
    expect(await screen.findByTestId("section-working")).toBeInTheDocument();
    expect(screen.getByTestId("presence")).toHaveTextContent("seat list unreadable");
  });

  it("keeps the seats on screen when a later poll of the board fails", async () => {
    vi.useFakeTimers();
    try {
      const { api, calls } = fakeApi({ ...STATES.fresh, failAfterFirst: { board: "the board stopped answering" } });
      render(<App api={api} />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });
      expect(screen.getByTestId("presence")).toHaveTextContent("worker");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(9000);
      });
      expect(calls.filter((c) => c === "board:alpha").length).toBeGreaterThan(1);
      expect(screen.getByTestId("presence")).toHaveTextContent("worker");
      expect(screen.getByTestId("freshness")).toHaveTextContent("last read failed: board");
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not keep one project's records after a switch, even when the new read fails", async () => {
    const user = userEvent.setup();
    const { api } = fakeApi({ ...STATES.accepted, failByProject: { plan: { demo: "no answer from the board" } } });
    render(<App api={api} />);
    expect(await screen.findByText("Sample: build the API on top of the model")).toBeInTheDocument();
    await user.selectOptions(screen.getByTestId("project-switch"), "demo");
    expect(await screen.findByText(/The plan could not be read/)).toBeInTheDocument();
    expect(screen.queryByText("Sample: build the API on top of the model")).toBeNull();
    const body = document.body.textContent || "";
    expect(body).not.toContain("Ship a small stats API");
    expect(body).not.toContain("1 of 3 accepted");
    expect(body).not.toContain("writing tests for the /stats endpoint");
  });

  it("does not keep one project's seats after a switch whose board read fails", async () => {
    const user = userEvent.setup();
    const { api } = fakeApi({
      ...STATES.fresh,
      board: { ...STATES.fresh.board, agents: [seat("planner"), seat("coder"), seat("nora")] },
      failByProject: { board: { demo: "no answer from the board" } },
    });
    render(<App api={api} />);
    await waitFor(() => expect(screen.getByTestId("presence")).toHaveTextContent("nora"));
    await user.selectOptions(screen.getByTestId("project-switch"), "demo");
    await waitFor(() => expect(screen.getByTestId("presence")).toHaveTextContent("seat list unreadable"));
    expect(document.body.textContent).not.toContain("nora");
    expect(screen.getByTestId("freshness")).toHaveTextContent("last read failed: board");
  });
});
