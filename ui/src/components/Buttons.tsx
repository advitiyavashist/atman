/**
 * The button model (spec §6.1): one verb per real <button>. A disabled button
 * always has its reason as visible text beside it, never only a tooltip. After
 * an action runs, the row is replaced by one result line in the past tense
 * with the actor and the proof the API returned.
 */

import type { ReactNode } from "react";

export const READ_ONLY_WHY = "Read-only. Start with atm ui --operator <name> to act.";

export interface Verb {
  label: ReactNode;
  /** The accessible name when the label carries markup (a sha). */
  name?: string;
  onClick: () => void;
  kind?: "primary" | "danger" | "quiet" | "";
  /** The visible reason this verb cannot run now. Set = disabled. */
  why?: string;
  busy?: boolean;
  testId?: string;
}

export function ActionButton({ v, readOnly }: { v: Verb; readOnly: boolean }) {
  const why = readOnly ? READ_ONLY_WHY : v.why || "";
  return (
    <button
      type="button"
      className={`btn ${v.kind || ""}`}
      disabled={!!why || !!v.busy}
      aria-label={v.name}
      title={why || undefined}
      onClick={v.onClick}
      data-testid={v.testId}
      aria-busy={v.busy || undefined}
    >
      {v.busy ? <span className="spin" aria-hidden="true" /> : null}
      {v.label}
    </button>
  );
}

/**
 * A row of verbs plus every distinct disabled reason, once. In the read-only
 * app the one reason replaces them all, since every verb shares it.
 */
export function ActionRow({ verbs, readOnly, children }: { verbs: Verb[]; readOnly: boolean; children?: ReactNode }) {
  if (!verbs.length && !children) return null;
  const reasons = readOnly ? [READ_ONLY_WHY] : Array.from(new Set(verbs.map((v) => v.why || "").filter(Boolean)));
  return (
    <div className="actions" role="group" aria-label="Actions">
      {verbs.map((v, i) => (
        <ActionButton key={i} v={v} readOnly={readOnly} />
      ))}
      {children}
      {reasons.map((r) => (
        <span key={r} className="why" data-testid="why">
          {r}
        </span>
      ))}
    </div>
  );
}

/** What an action recorded, in the API's words (§6.1): `Accepted · by boss · 3f2a1c0`. */
export interface ResultLine {
  /** The past-tense verb with the actor: "Accepted · by boss", "Assigned · to worker · by boss". */
  text: string;
  /** The proof, in monospace: the sha the verdict bound to. */
  proof?: string;
  tail?: string;
}

export function Result({ line }: { line: ResultLine }) {
  return (
    <span className="result" data-testid="result" role="status">
      {line.text}
      {line.proof ? (
        <>
          {" "}
          · <code>{line.proof}</code>
        </>
      ) : null}
      {line.tail ? ` · ${line.tail}` : ""}
    </span>
  );
}
