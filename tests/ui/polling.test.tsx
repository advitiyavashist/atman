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
import { describe, expect, it } from "vitest";

import { App } from "../../ui/src/App";
import { useResource } from "../../ui/src/state/useResource";
import { fakeApi } from "./support/fake";

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
    const { api } = fakeApi({
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
    const user = userEvent.setup();
    // The sidebar count comes from the first, good read.
    await user.click(await screen.findByRole("button", { name: /^Fleet 1/ }));
    // The seats an operator was reading are still there, and the freshness
    // line — not an empty pane — is what says the refresh failed.
    expect(await screen.findByTestId("seats")).toHaveTextContent("planner@alpha");
    await waitFor(() => expect(screen.getByTestId("freshness")).toHaveTextContent("last read failed: board"));
    expect(screen.getByLabelText("Fleet").textContent).not.toMatch(/could not be read/);
  });
});
