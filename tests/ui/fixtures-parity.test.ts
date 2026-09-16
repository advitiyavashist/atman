import { describe, expect, it } from "vitest";
import { existsSync, readdirSync, statSync } from "node:fs";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// T-1045: tests/fixtures/ is the only contract-fixture tree. The former
// copies under ui/src/fixtures/data and tests/ui/api/fixtures/data are gone
// so they cannot drift. Screen tests import the canonical JSON; the API
// client loader reads the same directory.

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..");
const canonicalDir = resolve(repoRoot, "tests", "fixtures");
const uiCopyDir = resolve(repoRoot, "ui", "src", "fixtures", "data");
const apiCopyDir = resolve(here, "api", "fixtures", "data");

function listJson(dir: string): string[] {
  if (!existsSync(dir)) return [];
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...listJson(full));
    else if (entry.endsWith(".json")) out.push(full);
  }
  return out;
}

describe("contract fixtures have one source of truth", () => {
  it("the canonical fixture pack is present", () => {
    expect(existsSync(resolve(canonicalDir, "manifest.json"))).toBe(true);
  });

  it("does not keep a second JSON tree under ui/src/fixtures/data", () => {
    expect(listJson(uiCopyDir)).toEqual([]);
  });

  it("does not keep a second JSON tree under tests/ui/api/fixtures/data", () => {
    expect(listJson(apiCopyDir)).toEqual([]);
  });

  it("keeps the screen domains the UI tests still import", () => {
    for (const dir of ["overview", "tickets", "agents", "activity", "master", "messages", "errors"]) {
      expect(existsSync(resolve(canonicalDir, dir))).toBe(true);
    }
  });
});
