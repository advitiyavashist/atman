/**
 * Fixtures generated FROM the contract, not typed out beside it.
 *
 * `fixture("plan.json", {...})` walks `docs/api/schemas/plan.json` — the same
 * file the Python contract test validates the server's real output against —
 * builds a minimal instance of it, merges the case under test on top, and then
 * validates the result with ajv. Two things follow, and both are the point:
 *
 * 1. A field that is added to the contract appears in every fixture at once,
 *    so a screen that ignores it is the only thing left to fix.
 * 2. A field a test *thinks* exists but the schema does not have fails loudly
 *    here, with the schema's own error, instead of rendering as undefined.
 *
 * The generator's defaults are deliberately the "nothing was recorded" branch:
 * a nullable number is `null`, a nullable object is `null`, a stamp is `""`.
 * Conditional subschemas (`if`/`then`/`else`) are then applied, which is how
 * `checked_at: ""` pulls `age: "age unknown"` in, and how `tokens: null`
 * pulls `tokens_label: "unknown"` in. A fixture cannot be more certain than
 * the contract allows.
 */

import Ajv2020 from "ajv/dist/2020";

import { BASE, SCHEMAS, explain, validatorFor } from "../../../ui/src/api/schemas";

type Json = unknown;
type Obj = Record<string, Json>;

const ajv = new Ajv2020({ allErrors: true, strict: false });
for (const s of Object.values(SCHEMAS)) ajv.addSchema(s);

/** Candidate strings tried against a `pattern`, most specific first. */
const STRING_CANDIDATES = ["T-1", "seat@project", "board", "x", "1"];

function pointer(doc: Obj, ptr: string): Json {
  let node: Json = doc;
  for (const raw of ptr.split("/")) {
    if (!raw || raw === "#") continue;
    const key = raw.replace(/~1/g, "/").replace(/~0/g, "~");
    node = (node as Obj)?.[key];
    if (node === undefined) throw new Error(`schema pointer ${ptr} does not resolve`);
  }
  return node;
}

interface Ctx {
  file: string;
}

function deref(node: Obj, ctx: Ctx): { schema: Obj; ctx: Ctx } {
  const ref = node.$ref as string | undefined;
  if (!ref) return { schema: node, ctx };
  const [fileRaw, ptr] = ref.split("#");
  const file = fileRaw ? fileRaw.replace(BASE, "") : ctx.file;
  const doc = SCHEMAS[file];
  if (!doc) throw new Error(`$ref ${ref} names a schema file this app does not load`);
  const target = ptr ? pointer(doc as Obj, ptr) : doc;
  return deref(target as Obj, { file });
}

function pickType(schema: Obj): string {
  const t = schema.type;
  if (typeof t === "string") return t;
  if (Array.isArray(t)) {
    // Prefer null: "the board recorded nothing" is the honest default.
    if (t.includes("null")) return "null";
    return String(t[0]);
  }
  if (schema.properties || schema.required) return "object";
  if (schema.items) return "array";
  return "string";
}

function stringFor(schema: Obj, where: string): string {
  const pattern = schema.pattern as string | undefined;
  const min = (schema.minLength as number | undefined) ?? 0;
  if (pattern) {
    const re = new RegExp(pattern);
    for (const cand of STRING_CANDIDATES) if (re.test(cand)) return cand;
    throw new Error(`no fixture string matches ${pattern} at ${where}; add one to STRING_CANDIDATES`);
  }
  if (min > 0) return "x".repeat(Math.max(min, 1));
  return "";
}

function subschemas(schema: Obj): Obj[] {
  const out: Obj[] = [];
  for (const key of ["allOf"] as const) {
    for (const s of (schema[key] as Obj[] | undefined) || []) out.push(s);
  }
  return out;
}

/** Every (if, then/else) pair a schema applies to one instance. */
function conditionals(schema: Obj): Array<{ if: Obj; then?: Obj; else?: Obj }> {
  const out: Array<{ if: Obj; then?: Obj; else?: Obj }> = [];
  const add = (s: Obj) => {
    if (s.if) out.push({ if: s.if as Obj, then: s.then as Obj | undefined, else: s.else as Obj | undefined });
  };
  add(schema);
  for (const s of subschemas(schema)) add(s);
  return out;
}

function matches(value: Json, sub: Obj): boolean {
  try {
    return !!ajv.validate(sub, value);
  } catch {
    return false;
  }
}

let checkSeq = 0;

/**
 * Validate against a fragment of a schema document.
 *
 * The fragment's relative `$ref`s (`common.json#/$defs/blocker`) only resolve
 * against a base, so the fragment is wrapped in a throwaway `$id` under the
 * contract's own base.
 */
function matchesFragment(value: Json, node: Obj): boolean {
  const wrapped: Obj = { ...node, $id: `${BASE}fixture-check-${(checkSeq += 1)}.json` };
  delete wrapped.$schema;
  try {
    return !!ajv.validate(wrapped, value);
  } catch {
    return false;
  }
}

function generate(node: Obj, ctx: Ctx, where: string): Json {
  const { schema, ctx: at } = deref(node, ctx);
  if (schema.const !== undefined) return schema.const as Json;
  if (Array.isArray(schema.enum)) return (schema.enum as Json[])[0];
  for (const key of ["oneOf", "anyOf"] as const) {
    const branches = schema[key] as Obj[] | undefined;
    if (branches?.length) {
      // The first branch that is not "null" would over-claim; the first branch
      // full stop is what the contract lists first, and null (when listed) is
      // the honest default.
      const nullish = branches.find((b) => b.type === "null");
      return generate(nullish || branches[0], at, `${where}.${key}`);
    }
  }
  const type = pickType(schema);
  if (type === "null") return null;
  if (type === "boolean") return false;
  if (type === "integer" || type === "number") {
    const min = (schema.minimum as number | undefined) ?? 0;
    return min;
  }
  if (type === "string") return stringFor(schema, where);
  if (type === "array") {
    const n = (schema.minItems as number | undefined) ?? 0;
    const items = schema.items as Obj | undefined;
    const out: Json[] = [];
    for (let i = 0; i < n; i += 1) out.push(items ? generate(items, at, `${where}[${i}]`) : null);
    return out;
  }
  // object
  const out: Obj = {};
  const props = (schema.properties as Record<string, Obj> | undefined) || {};
  const required = new Set<string>((schema.required as string[] | undefined) || []);
  for (const s of subschemas(schema)) {
    for (const r of (s.required as string[] | undefined) || []) required.add(r);
    Object.assign(props, (s.properties as Record<string, Obj> | undefined) || {});
  }
  for (const key of required) {
    const prop = props[key];
    out[key] = prop ? generate(prop, at, `${where}.${key}`) : null;
  }
  applyConditionals(out, schema, props, at, where);
  return out;
}

/**
 * Make the generated object obey the schema's own `if`/`then`/`else`.
 *
 * This is why a fixture can be trusted: the contract's conditional rules (an
 * unaged usage reading has no percentage, a null token count is labelled
 * "unknown", a board with a lead has a status and no picker) are applied here
 * rather than copied into each test by hand.
 */
function applyConditionals(
  out: Obj,
  schema: Obj,
  props: Record<string, Obj>,
  ctx: Ctx,
  where: string,
  keep: ReadonlySet<string> = new Set(),
): void {
  for (let pass = 0; pass < 4; pass += 1) {
    let changed = false;
    for (const cond of conditionals(schema)) {
      const branch = matches(out, cond.if) ? cond.then : cond.else;
      if (!branch) continue;
      for (const r of (branch.required as string[] | undefined) || []) {
        if (!(r in out) && props[r]) {
          out[r] = generate(props[r], ctx, `${where}.${r}`);
          changed = true;
        }
      }
      const want = (branch.properties as Record<string, Obj> | undefined) || {};
      for (const [key, sub] of Object.entries(want)) {
        if (matches(out[key], sub)) continue;
        // A value the caller asked for is never rewritten: if it contradicts
        // the contract, validation says so instead of the fixture hiding it.
        if (keep.has(key)) continue;
        out[key] = coerce(out[key], sub, props[key], ctx, `${where}.${key}`);
        changed = true;
      }
    }
    if (!changed) return;
  }
}

/** The `properties` map of a schema, including any inside `allOf`. */
function propsOf(schema: Obj): Record<string, Obj> {
  const props: Record<string, Obj> = { ...((schema.properties as Record<string, Obj> | undefined) || {}) };
  for (const s of subschemas(schema)) Object.assign(props, (s.properties as Record<string, Obj> | undefined) || {});
  return props;
}

/** A value that satisfies both the property's own schema and a conditional's. */
function coerce(current: Json, sub: Obj, prop: Obj | undefined, ctx: Ctx, where: string): Json {
  if (sub.const !== undefined) return sub.const as Json;
  if (Array.isArray(sub.enum)) return (sub.enum as Json[])[0];
  if (sub.type === "null") return null;
  if (sub.maxItems === 0) return [];
  if (prop) {
    const { schema, ctx: at } = deref(prop, ctx);
    const branches = ((schema.oneOf || schema.anyOf) as Obj[] | undefined) || [];
    for (const b of branches) {
      const candidate = generate(b, at, where);
      if (matches(candidate, sub)) return candidate;
    }
    const merged = generate({ ...schema, type: sub.type ?? schema.type } as Obj, at, where);
    if (matches(merged, sub)) return merged;
  }
  const guess = generate(sub, ctx, where);
  if (matches(guess, sub)) return guess;
  throw new Error(`cannot satisfy ${JSON.stringify(sub)} at ${where}; current ${JSON.stringify(current)}`);
}

function isPlainObject(v: Json): v is Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Overrides win; arrays are replaced whole, objects merged key by key. */
function merge(base: Json, over: Json): Json {
  if (!isPlainObject(base) || !isPlainObject(over)) return over === undefined ? base : over;
  const out: Obj = { ...base };
  for (const [k, v] of Object.entries(over)) out[k] = k in base ? merge(base[k], v) : v;
  return out;
}

export type Deep<T> = T extends Array<infer _I>
  ? T
  : T extends object
    ? { [K in keyof T]?: Deep<T[K]> }
    : T;

/**
 * A minimal valid instance of one schema file, with `overrides` merged in and
 * the result validated. An override the schema refuses throws here.
 */
export function fixture<T>(file: string, overrides?: Deep<T> | Partial<T>): T {
  const doc = SCHEMAS[file];
  if (!doc) throw new Error(`unknown schema ${file}`);
  const base = generate(doc as Obj, { file }, file);
  const out = merge(base, (overrides ?? {}) as Json) as Obj;
  // An override can flip a conditional (needs_lead, accepted, tokens), so the
  // schema's own if/then/else is applied again over the merged instance —
  // never over a key the caller set.
  if (isPlainObject(out)) {
    applyConditionals(out, doc as Obj, propsOf(doc as Obj), { file }, file, new Set(Object.keys(overrides ?? {})));
  }
  const validate = validatorFor(file);
  if (!validate(out)) {
    throw new Error(`fixture for ${file} does not satisfy its own schema: ${explain(validate)}`);
  }
  return out as T;
}

/**
 * An instance of a subschema inside a route schema, by JSON pointer — e.g.
 * `subFixture("ticket.json", "/properties/runs/items")` for one run row, or
 * `subFixture("plan.json", "/properties/nodes/items")` for one plan step.
 *
 * Rows that the contract defines inline (runs, plan nodes, needs-you items)
 * have no `$defs` entry to point at, and copying their fields into a test
 * would be a second contract. This reads the real one.
 */
export function subFixture<T>(file: string, ptr: string, overrides?: Deep<T> | Partial<T>): T {
  const doc = SCHEMAS[file];
  if (!doc) throw new Error(`unknown schema ${file}`);
  const node = pointer(doc as Obj, ptr) as Obj;
  const base = generate(node, { file }, `${file}${ptr}`);
  const out = merge(base, (overrides ?? {}) as Json);
  if (!matchesFragment(out, node)) {
    throw new Error(`${file}${ptr} fixture does not satisfy its subschema: ${JSON.stringify(out)}`);
  }
  return out as T;
}

/** A sub-object of common.json, e.g. `defFixture("post", {...})`. */
export function defFixture<T>(name: string, overrides?: Deep<T> | Partial<T>): T {
  const def = pointer(SCHEMAS["common.json"] as Obj, `/$defs/${name}`) as Obj;
  const base = generate(def, { file: "common.json" }, `common.json#/$defs/${name}`);
  const out = merge(base, (overrides ?? {}) as Json);
  if (!matches(out, { $ref: `${BASE}common.json#/$defs/${name}` })) {
    throw new Error(`${name} fixture does not satisfy common.json#/$defs/${name}: ${JSON.stringify(out)}`);
  }
  return out as T;
}

/** Assert a value against a schema file, loudly, from a test. */
export function expectValid(file: string, value: unknown): void {
  const validate = validatorFor(file);
  if (!validate(value)) throw new Error(`${file}: ${explain(validate)}`);
}
