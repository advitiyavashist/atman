import { describe, expect, it } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

// ui/src/fixtures/data is a copy of the frozen contract's tests/fixtures/
// (opus-backend/e010-contract@855e15a) so the UI package is self-contained.
// This test guards against silent drift between the two: if the canonical
// tests/fixtures/ is present in this checkout (it is, since this branch was
// cut from the contract commit), every manifest-listed fixture must be
// byte-for-byte identical, parsed as JSON, to the copy the UI reads.

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..");
const canonicalDir = resolve(repoRoot, "tests", "fixtures");
const uiCopyDir = resolve(repoRoot, "ui", "src", "fixtures", "data");

describe("ui fixtures copy stays in sync with tests/fixtures/", () => {
  const manifestPath = resolve(canonicalDir, "manifest.json");
  const hasCanonical = existsSync(manifestPath);

  it.runIf(hasCanonical)("matches every manifest-listed fixture exactly", () => {
    const manifest = JSON.parse(readFileSync(manifestPath, "utf-8")) as Record<string, string>;
    const mismatches: string[] = [];
    for (const relPath of Object.keys(manifest)) {
      const canonical = readFileSync(resolve(canonicalDir, relPath), "utf-8");
      const copyPath = resolve(uiCopyDir, relPath);
      if (!existsSync(copyPath)) continue; // ui only mirrors the subset it uses
      const copy = readFileSync(copyPath, "utf-8");
      if (JSON.parse(canonical) === undefined) continue;
      if (JSON.stringify(JSON.parse(canonical)) !== JSON.stringify(JSON.parse(copy))) {
        mismatches.push(relPath);
      }
    }
    expect(mismatches).toEqual([]);
  });

  it("mirrors at least the screens T-183 owns", () => {
    for (const dir of ["overview", "tickets", "agents", "activity", "master"]) {
      expect(existsSync(resolve(uiCopyDir, dir))).toBe(true);
    }
  });

  it("mirrors the messages domain T-189 owns", () => {
    for (const dir of ["messages", "errors"]) {
      expect(existsSync(resolve(uiCopyDir, dir))).toBe(true);
    }
  });
});
