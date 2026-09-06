import { afterEach, describe, expect, it } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../../ui/src/App";

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

describe("App shell", () => {
  it("labels the whole app as fixture mode with no live data", () => {
    render(<App />);
    expect(screen.getByText("FIXTURE MODE · NO LIVE DATA")).toBeInTheDocument();
  });

  it("navigates between the five screens via the nav links and updates aria-current", async () => {
    const user = userEvent.setup();
    render(<App />);
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
    await user.click(screen.getByRole("link", { name: "Agents" }));
    expect(await screen.findByRole("heading", { name: "Agents" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Agents" })).toHaveAttribute("aria-current", "page");
  });

  it("toggles theme and reflects it as a pressed state", async () => {
    const user = userEvent.setup();
    render(<App />);
    const toggle = screen.getByRole("button", { name: "Light mode" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await user.click(toggle);
    expect(document.body.classList.contains("light")).toBe(true);
    expect(screen.getByRole("button", { name: "Dark mode" })).toHaveAttribute("aria-pressed", "true");
  });

  it("opens Connect agent as a keyboard-reachable dialog and closes it on Escape", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Connect agent" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByLabelText("Agent name")).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("never asserts sub-50ms latency or a beats-Presidio claim anywhere in the shell", () => {
    const { container } = render(<App />);
    expect(container.textContent).not.toMatch(/50\s*ms/i);
    expect(container.textContent).not.toMatch(/presidio/i);
    expect(container.textContent).not.toMatch(/\benforces\b/i);
  });
});
