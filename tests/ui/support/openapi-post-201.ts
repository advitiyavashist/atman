import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..", "..");
const specPath = resolve(repoRoot, "docs", "contracts", "openapi.yaml");

/** POST path templates from openapi.yaml whose success response is 201. */
export function loadPost201PathTemplates(): string[] {
  const raw = execSync(
    `python3 -c 'import json,sys,yaml;from pathlib import Path;spec=yaml.safe_load(Path(sys.argv[1]).read_text());print(json.dumps(sorted(p for p,i in spec["paths"].items() if i.get("post") and "201" in i["post"].get("responses",{}))))' ${JSON.stringify(specPath)}`,
    { encoding: "utf-8" },
  );
  return JSON.parse(raw) as string[];
}

/**
 * Suffix the render-live mock matches with `path.endsWith(suffix)`.
 * Static paths use the full path; parameterized paths use the trailing static segment.
 */
export function createdRouteSuffix(pathTemplate: string): string {
  const segments = pathTemplate.split("/").filter(Boolean);
  const tail: string[] = [];
  for (let i = segments.length - 1; i >= 0; i--) {
    if (segments[i].startsWith("{")) break;
    tail.unshift(segments[i]);
  }
  return `/${tail.join("/")}`;
}

/** Derive the CREATED_ROUTES set the mock should expose for every contract POST+201 path. */
export function expectedCreatedRouteSuffixes(): string[] {
  const suffixes = loadPost201PathTemplates().map(createdRouteSuffix);
  return [...new Set(suffixes)].sort();
}

/** Paths the live screen mock covers today (not every contract POST+201 route yet). */
export const MOCK_SCOPED_POST_201_SUFFIXES = [
  "/assignments",
  "/enrollments",
  "/invitations",
  "/invitations/exchange",
  "/reviews",
  "/sessions",
  "/tickets",
  "/updates",
] as const;

export function assertOpenapiSpecReadable(): void {
  readFileSync(specPath, "utf-8");
}
