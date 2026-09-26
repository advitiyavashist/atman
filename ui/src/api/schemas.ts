/**
 * The contract, loaded from `docs/api/schemas/` — the same files
 * `tests/test_t1103_app_api.py` validates the server's real output against.
 *
 * There is no second copy of the contract in this app. The dev-time response
 * check (`devCheck.ts`) and the test fixtures (`tests/ui/support/schema.ts`)
 * both build from these objects, so a field that leaves the schema also leaves
 * the fixtures, and the tests fail instead of quietly rendering nothing.
 */

import Ajv2020, { type ValidateFunction } from "ajv/dist/2020";

import board from "../../../docs/api/schemas/board.json";
import common from "../../../docs/api/schemas/common.json";
import error from "../../../docs/api/schemas/error.json";
import leadRequest from "../../../docs/api/schemas/lead-request.json";
import leadResponse from "../../../docs/api/schemas/lead-response.json";
import lead from "../../../docs/api/schemas/lead.json";
import needsYou from "../../../docs/api/schemas/needs-you.json";
import plan from "../../../docs/api/schemas/plan.json";
import projects from "../../../docs/api/schemas/projects.json";
import session from "../../../docs/api/schemas/session.json";
import thread from "../../../docs/api/schemas/thread.json";
import ticket from "../../../docs/api/schemas/ticket.json";

export const SCHEMAS: Record<string, Record<string, unknown>> = {
  "board.json": board as Record<string, unknown>,
  "common.json": common as Record<string, unknown>,
  "error.json": error as Record<string, unknown>,
  "lead-request.json": leadRequest as Record<string, unknown>,
  "lead-response.json": leadResponse as Record<string, unknown>,
  "lead.json": lead as Record<string, unknown>,
  "needs-you.json": needsYou as Record<string, unknown>,
  "plan.json": plan as Record<string, unknown>,
  "projects.json": projects as Record<string, unknown>,
  "session.json": session as Record<string, unknown>,
  "thread.json": thread as Record<string, unknown>,
  "ticket.json": ticket as Record<string, unknown>,
};

/** The `$id` base every schema shares, so relative `$ref`s resolve offline. */
export const BASE = "https://schemas.atman.invalid/app-v2/";

let ajv: Ajv2020 | null = null;

function instance(): Ajv2020 {
  if (ajv) return ajv;
  const a = new Ajv2020({ allErrors: true, strict: false });
  for (const schema of Object.values(SCHEMAS)) a.addSchema(schema);
  ajv = a;
  return a;
}

/** A compiled validator for one schema file, e.g. "plan.json". */
export function validatorFor(file: string): ValidateFunction {
  const v = instance().getSchema(BASE + file);
  if (!v) throw new Error(`no schema named ${file}; known: ${Object.keys(SCHEMAS).join(", ")}`);
  return v;
}

/** One readable line per schema violation. */
export function explain(v: ValidateFunction): string {
  return (v.errors || [])
    .map((e) => `${e.instancePath || "/"} ${e.message}${e.params ? ` ${JSON.stringify(e.params)}` : ""}`)
    .join("; ");
}
