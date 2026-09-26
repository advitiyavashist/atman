/**
 * Dev-only: validate every response against its JSON Schema and say so loudly.
 *
 * `client.ts` reaches this module through a dynamic import behind
 * `if (!import.meta.env.DEV) return;`, so a production build drops the branch
 * and ships neither ajv nor the schemas. `ui/README.md` records how that is
 * checked after a build.
 *
 * A violation is printed once per route (a poll would otherwise repeat it
 * every few seconds) and does not throw: a wrong field must not blank the
 * screen for an operator who needs the rest of it. The loud failure belongs to
 * the tests, where a missing field is an assertion, not a console line.
 */

import { explain, validatorFor } from "./schemas";

const said = new Set<string>();

export function checkAgainstSchema(file: string, body: unknown, url: string): void {
  const validate = validatorFor(file);
  if (validate(body)) return;
  const line = `${file}: ${explain(validate)}`;
  if (said.has(line)) return;
  said.add(line);
  // eslint-disable-next-line no-console
  console.error(`[atman] ${url} does not match ${line}`);
}
