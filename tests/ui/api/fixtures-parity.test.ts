import { describe, expect, it } from "vitest";
import { readFileSync, existsSync, readdirSync, statSync } from "node:fs";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// tests/ui/api/fixtures/data is a trimmed copy of the frozen contract's
// tests/fixtures/ (opus-backend/e010-contract@855e15a, refreshed against
// tickets origin/main@884be46) scoped to the routes this client's tests
// exercise — a different subset than ui/src/fixtures/data (T-183's, scoped
// to screen scenarios). Same drift guard as tests/ui/fixtures-parity.test.ts.

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..", "..");
const canonicalDir = resolve(repoRoot, "tests", "fixtures");
const apiCopyDir = resolve(here, "fixtures", "data");

function listFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...listFiles(full));
    else out.push(full);
  }
  return out;
}

describe("api client fixtures copy stays in sync with tests/fixtures/", () => {
  const manifestPath = resolve(canonicalDir, "manifest.json");

  // The canonical pack going missing (a bad checkout, a moved fixtures dir)
  // is exactly the moment this guard must be loud, not skip quietly — a
  // runIf(hasCanonical) here would turn "the drift guard is gone" into a
  // silent pass instead of a failure.
  it("the canonical fixture pack this copy is checked against is present", () => {
    expect(existsSync(manifestPath)).toBe(true);
  });

  it("every fixture this client's tests bundle matches its canonical original byte-for-byte", () => {
    const copiedFiles = listFiles(apiCopyDir).map((f) => f.slice(apiCopyDir.length + 1));
    expect(copiedFiles.length).toBeGreaterThan(0);
    const mismatches: string[] = [];
    const missingFromManifest: string[] = [];
    const manifest = JSON.parse(readFileSync(manifestPath, "utf-8")) as Record<string, string>;
    for (const relPath of copiedFiles) {
      const posixPath = relPath.split("\\").join("/");
      if (!(posixPath in manifest)) {
        missingFromManifest.push(posixPath);
        continue;
      }
      // Raw buffer comparison, not JSON.stringify(JSON.parse(...)) — that
      // compares parsed values, not bytes, so it would wave through a
      // reformatted copy that "byte-for-byte" (this file's own docstring
      // and README.md's claim) says cannot happen.
      const canonical = readFileSync(resolve(canonicalDir, posixPath));
      const copy = readFileSync(resolve(apiCopyDir, posixPath));
      if (!canonical.equals(copy)) {
        mismatches.push(posixPath);
      }
    }
    expect(missingFromManifest).toEqual([]);
    expect(mismatches).toEqual([]);
  });
});
