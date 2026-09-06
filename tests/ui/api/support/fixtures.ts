import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const dataDir = resolve(here, "..", "fixtures", "data");

/** Loads a fixture from the byte-for-byte, parity-checked copy in ./fixtures/data. */
export function loadFixture<T = unknown>(relPath: string): T {
  return JSON.parse(readFileSync(resolve(dataDir, relPath), "utf-8")) as T;
}
