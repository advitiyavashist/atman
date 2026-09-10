import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../../ui/src/App";
import { TEST_SESSION, boardFetch } from "./support/render-live";
import overviewPopulated from "../../ui/src/fixtures/data/overview/populated.json";
import agentsPopulated from "../../ui/src/fixtures/data/agents/list-populated.json";

afterEach(() => {
  cleanup();
  window.location.hash = "";
  document.body.classList.remove("light");
  try {
    localStorage.clear();
  } catch {
    /* ignore */
  }
});

function appWith(routes: Record<string, unknown>) {
  const harness = boardFetch(routes);
  const session = {
    ...TEST_SESSION,
    config: { ...TEST_SESSION.config, fetch: harness.fetchMock as unknown as typeof fetch },
  };
  return { harness, ui: <App session={session} reconnectDelayMs={50_000} /> };
}

const routes = { "GET /overview": overviewPopulated, "GET /agents": agentsPopulated };

describe("App shell", () => {
  it("no longer labels itself fixture mode, because it reads a live board", async () => {
    const { ui } = appWith(routes);
    render(ui);
    await waitFor(() => expect(screen.getByTestId("count-ready")).toBeInTheDocument());
    expect(screen.queryByText("FIXTURE MODE · NO LIVE DATA")).not.toBeInTheDocument();
    expect(screen.getByTestId("board-label").textContent).toMatch(/^workspace · /);
  });

  it("navigates between screens via the nav links and updates aria-current", async () => {
    const user = userEvent.setup();
    const { ui } = appWith(routes);
    render(ui);
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
    await user.click(screen.getByRole("link", { name: "Agents" }));
    expect(await screen.findByRole("heading", { name: "Agents" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Agents" })).toHaveAttribute("aria-current", "page");
  });

  it("toggles theme and reflects it as a pressed state", async () => {
    const user = userEvent.setup();
    const { ui } = appWith(routes);
    render(ui);
    const toggle = screen.getByRole("button", { name: "Light mode" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await user.click(toggle);
    expect(document.body.classList.contains("light")).toBe(true);
    expect(screen.getByRole("button", { name: "Dark mode" })).toHaveAttribute("aria-pressed", "true");
  });

  it("opens Connect agent as a keyboard-reachable dialog and closes it on Escape", async () => {
    const user = userEvent.setup();
    const { ui } = appWith(routes);
    render(ui);
    await user.click(screen.getAllByRole("button", { name: "Connect agent" })[0]);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByLabelText("Agent name")).toHaveFocus();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("uses the atman wordmark and formation-dots, not Steer caret chrome", async () => {
    const { ui } = appWith(routes);
    const { container } = render(ui);
    await waitFor(() => expect(screen.getByTestId("count-ready")).toBeInTheDocument());
    expect(container.querySelector(".wordmark")?.textContent).toBe("atman");
    expect(container.querySelectorAll("a.brand circle").length).toBeGreaterThanOrEqual(5);
    expect(container.textContent).not.toMatch(/↗ Ticket Board/);
    expect(container.textContent).not.toMatch(/Ticket Board/);
  });

  it("never asserts sub-50ms latency or a beats-Presidio claim anywhere in the shell", async () => {
    const { ui } = appWith(routes);
    const { container } = render(ui);
    await waitFor(() => expect(screen.getByTestId("count-ready")).toBeInTheDocument());
    expect(container.textContent).not.toMatch(/50\s*ms/i);
    expect(container.textContent).not.toMatch(/presidio/i);
    expect(container.textContent).not.toMatch(/\benforces\b/i);
  });

  it("says it is not connected rather than rendering an empty board when there is no session", async () => {
    // No host, no injected session, no URL token: the discovery probe 404s.
    const probe = vi.fn(async () => new Response("nope", { status: 404 }));
    vi.stubGlobal("fetch", probe);
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("unconfigured")).toBeInTheDocument());
    // The distinction that matters: this must not read as a quiet board.
    expect(screen.getByTestId("unconfigured").textContent).toMatch(/not an empty board/i);
    expect(screen.queryByTestId("count-ready")).not.toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});
