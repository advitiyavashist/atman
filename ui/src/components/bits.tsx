/** Small shared pieces: a chip, a copy button, a labelled field, a missing value. */

import { useEffect, useRef, useState } from "react";
import { NOT_RECORDED } from "../lib/format";
import type { Tone } from "../lib/map";

export function Chip({
  tone = "neutral",
  title,
  children,
}: {
  tone?: Tone;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <span className={`chip chip-${tone}`} title={title}>
      {children}
    </span>
  );
}

/**
 * A value the board does not hold.
 *
 * It is rendered, visibly, rather than left blank: an operator must be able to
 * tell "nobody recorded this" from "this is zero".
 */
export function Missing({ what = NOT_RECORDED, title }: { what?: string; title?: string }) {
  return (
    <span className="missing" title={title || "the board holds no value for this"}>
      {what}
    </span>
  );
}

/** Copies text to the clipboard and says so, or says it could not. */
export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [said, setSaid] = useState("");
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setSaid("copied");
    } catch {
      setSaid("could not copy — select it instead");
    }
    timer.current = window.setTimeout(() => setSaid(""), 2500);
  }
  return (
    <span className="copy">
      <button type="button" className="btn-quiet" onClick={copy} aria-label={`${label}: ${text.slice(0, 60)}`}>
        {label}
      </button>
      <span className="copy-said" role="status">
        {said}
      </span>
    </span>
  );
}

/** A monospace command line with its own copy button. */
export function Command({ cmd, note }: { cmd: string; note?: string }) {
  if (!cmd) return null;
  return (
    <div className="cmd">
      <code>{cmd}</code>
      <CopyButton text={cmd} />
      {note ? <p className="cmd-note">{note}</p> : null}
    </div>
  );
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="field">
      <span className="field-label">{label}</span>
      <span className="field-value">{children}</span>
    </div>
  );
}

export function Panel({
  title,
  children,
  aside,
  id,
}: {
  title: string;
  children: React.ReactNode;
  aside?: React.ReactNode;
  id?: string;
}) {
  return (
    <section className="panel" aria-labelledby={id ? `${id}-h` : undefined} id={id}>
      <header className="panel-head">
        <h2 id={id ? `${id}-h` : undefined}>{title}</h2>
        {aside}
      </header>
      <div className="panel-body">{children}</div>
    </section>
  );
}

/** A loud, plain failure. Never a blank screen pretending there is no data. */
export function Failure({ what, error }: { what: string; error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <p className="failure" role="alert">
      <strong>{what} could not be read.</strong> {msg}
    </p>
  );
}
