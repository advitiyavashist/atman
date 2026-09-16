import { describe, expect, it } from "vitest";
import { existsSync, readdirSync, statSync } from "node:fs";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { loadFixture } from "./support/fixtures";

// T-1045: API client tests load tests/fixtures/ directly. The trimmed copy
// that used to live next to this file is gone so it cannot drift.

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..", "..");
const canonicalDir = resolve(repoRoot, "tests", "fixtures");
const apiCopyDir = resolve(here, "fixtures", "data");

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

describe("api client fixtures read the canonical pack", () => {
  it("the canonical fixture pack this loader reads is present", () => {
    expect(existsSync(resolve(canonicalDir, "manifest.json"))).toBe(true);
  });

  it("does not keep a second JSON tree under tests/ui/api/fixtures/data", () => {
    expect(listJson(apiCopyDir)).toEqual([]);
  });

  it("loadFixture returns a canonical payload", () => {
    const fixture = loadFixture<{ project: { id: string } }>("overview/populated.json");
    expect(fixture.project.id).toBe("prj_demo0001");
  });
});
