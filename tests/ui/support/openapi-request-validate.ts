import { execFileSync } from "node:child_process";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..", "..");
const specPath = resolve(repoRoot, "docs", "contracts", "openapi.yaml");
const scriptPath = resolve(here, "openapi_request_validate.py");

export type Post201RequestSchemas = Record<string, string>;

let cachedSchemas: Post201RequestSchemas | null = null;

function runPython(command: string, extraArgs: string[] = [], input?: string): string {
  const args = [scriptPath, specPath, command, ...extraArgs];
  return execFileSync("python3", args, { encoding: "utf-8", input }).trim();
}

/** POST+201 path suffix -> request schema name, derived from openapi.yaml. */
export function loadPost201RequestSchemas(): Post201RequestSchemas {
  if (cachedSchemas) return cachedSchemas;
  cachedSchemas = JSON.parse(runPython("schemas")) as Post201RequestSchemas;
  return cachedSchemas;
}

/** Longest suffix match for a concrete request path against contract POST+201 routes. */
export function matchPost201RequestSchema(path: string): string | null {
  const schemas = loadPost201RequestSchemas();
  let schema: string | null = null;
  let bestLen = -1;
  for (const [suffix, name] of Object.entries(schemas)) {
    if (path.endsWith(suffix) && suffix.length > bestLen) {
      schema = name;
      bestLen = suffix.length;
    }
  }
  return schema;
}

/** Validate a parsed JSON body against a named component schema. Empty list means valid. */
export function validatePostRequestBody(schemaName: string, body: unknown): string[] {
  return JSON.parse(runPython("validate", [schemaName], JSON.stringify(body))) as string[];
}
