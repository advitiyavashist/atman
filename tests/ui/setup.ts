import "@testing-library/jest-dom/vitest";
import { beforeEach } from "vitest";

// The shell keeps the current view in the URL hash, and jsdom shares one
// location across the tests in a file. Each test starts from the default view.
beforeEach(() => {
  if (typeof window !== "undefined" && window.location.hash) window.location.hash = "";
  // Drafts, the Done fold and the panel width live in localStorage; one test's
  // choices must not become the next test's starting state.
  try {
    window.localStorage.clear();
  } catch {
    /* no storage in this environment */
  }
});

// jsdom has no clipboard. The copy buttons are real controls in these tests,
// so give them something that records what was copied rather than throwing.
const copied: string[] = [];
Object.defineProperty(navigator, "clipboard", {
  value: { writeText: (t: string) => (copied.push(t), Promise.resolve()) },
  configurable: true,
});
(globalThis as { __copied?: string[] }).__copied = copied;
