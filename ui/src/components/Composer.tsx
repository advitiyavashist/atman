/**
 * The composer (spec §5.6): Enter sends, Shift+Enter (or any modifier) adds a
 * new line, nothing is sent while an IME composition is active, empty text is
 * never sent. `@` and `#` open an autocomplete above the caret; Enter while
 * it is open picks and never sends. Unsent text is kept per (board, recipient,
 * about) in the browser only.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { AgentRow, PlanNode } from "../api/types";
import { bucketOf, phaseWord, presenceOf } from "../v3/model";
import { draftKey, storeGet, storeSet } from "../v3/store";
import { READ_ONLY_WHY } from "./Buttons";

export interface Sent {
  to: string;
  about: string;
  /** What the server answered: the posted record's summary line. */
  posted: string;
}

export interface ComposerProps {
  id: string;
  project: string;
  readOnly: boolean;
  /** A seat name, or "everyone". */
  recipient: string;
  /** A ticket id, or "" for no ticket. */
  about: string;
  /** The panel's composer: `about` is fixed to its ticket. */
  fixedAbout?: boolean;
  onAbout?: (id: string) => void;
  agents: ReadonlyArray<AgentRow>;
  nodes: ReadonlyArray<PlanNode>;
  send: (text: string) => Promise<Sent>;
  /** Bump to focus the box (Message / Answer). */
  focusToken?: number;
}

interface Ac {
  kind: "@" | "#";
  start: number;
  items: Array<{ k: string; d: string; dot: string }>;
  idx: number;
}

export function Composer(p: ComposerProps) {
  const key = draftKey(p.project, p.recipient, p.about);
  const [text, setText] = useState<string>(() => storeGet<string>(key, ""));
  const [ac, setAc] = useState<Ac | null>(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<Sent | null>(null);
  const [error, setError] = useState<string>("");
  const box = useRef<HTMLTextAreaElement>(null);
  const lastKey = useRef(key);
  // Where the caret goes once a pick has been rendered: set in the same
  // frame as the new text, so the next keystroke lands after the insertion.
  const caret = useRef<number | null>(null);
  useLayoutEffect(() => {
    const el = box.current;
    if (caret.current === null || !el) return;
    el.focus();
    el.setSelectionRange(caret.current, caret.current);
    caret.current = null;
  }, [text]);

  // Switching recipient or about swaps the draft in and out (§5.6).
  useEffect(() => {
    if (lastKey.current === key) return;
    lastKey.current = key;
    setText(storeGet<string>(key, ""));
    setAc(null);
    setSent(null);
    setError("");
  }, [key]);

  useEffect(() => {
    if (p.focusToken) box.current?.focus();
  }, [p.focusToken]);

  const update = useCallback(
    (value: string, caret: number) => {
      setText(value);
      storeSet(key, value);
      const upto = value.slice(0, caret);
      const m = /(^|\s)([@#])([\w-]*)$/.exec(upto);
      if (!m) {
        setAc(null);
        return;
      }
      const kind = m[2] as "@" | "#";
      const term = m[3].toLowerCase();
      const items =
        kind === "@"
          ? [{ k: "everyone", d: "the whole board", dot: "" }]
              .concat(
                p.agents.map((a) => {
                  const pr = presenceOf(a, p.nodes);
                  return { k: a.name, d: pr.words, dot: pr.kind };
                }),
              )
              .filter((i) => i.k.toLowerCase().startsWith(term))
          : p.nodes
              .map((n) => ({
                k: n.id,
                d: `${n.title} · ${phaseWord(n)} · ${n.owner || "nobody"}`,
                dot: bucketOf(n),
              }))
              .filter((i) => `${i.k} ${i.d}`.toLowerCase().includes(term));
      if (!items.length) {
        setAc(null);
        return;
      }
      setAc({ kind, start: upto.length - m[3].length - 1, items, idx: 0 });
    },
    [key, p.agents, p.nodes],
  );

  const pick = useCallback(
    (n: number) => {
      if (!ac || !box.current) return;
      const item = ac.items[n];
      const el = box.current;
      const before = text.slice(0, ac.start);
      const after = text.slice(el.selectionStart);
      const ins = `${ac.kind}${item.k} `;
      const next = before + ins + after;
      caret.current = before.length + ins.length;
      setText(next);
      setAc(null);
      if (ac.kind === "#" && !p.fixedAbout && !p.about && p.onAbout) {
        // The text moves with the draft to the ticket it now names (§5.6):
        // the new key holds it, the old key is cleared, and the about control follows.
        storeSet(draftKey(p.project, p.recipient, item.k), next);
        storeSet(key, "");
        p.onAbout(item.k);
      } else {
        storeSet(key, next);
      }
    },
    [ac, key, p, text],
  );

  async function submit() {
    if (p.readOnly || busy) return;
    const body = text.trim();
    if (!body) {
      box.current?.focus();
      return;
    }
    setBusy(true);
    setError("");
    try {
      const out = await p.send(body);
      setText("");
      storeSet(key, "");
      setSent(out);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (ac) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setAc({ ...ac, idx: (ac.idx + 1) % ac.items.length });
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setAc({ ...ac, idx: (ac.idx - 1 + ac.items.length) % ac.items.length });
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        // Enter picks; it never sends while the list is open.
        e.preventDefault();
        pick(ac.idx);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setAc(null);
        return;
      }
    }
    if (e.key !== "Enter" || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return;
    // While an IME composition is active, Enter belongs to the editor.
    const native = e.nativeEvent as unknown as { isComposing?: boolean; keyCode?: number };
    if (native.isComposing || native.keyCode === 229) return;
    e.preventDefault();
    void submit();
  }

  const placeholder = `Message ${p.recipient === "everyone" ? "everyone" : p.recipient}${p.fixedAbout && p.about ? ` about ${p.about}` : ""}…`;
  const aboutOptions = useMemo(() => p.nodes.filter((n) => n.phase !== "discarded"), [p.nodes]);

  return (
    <div className="composer" data-testid={p.id}>
      <label className="sr-only" htmlFor={p.id}>
        {placeholder}
      </label>
      <textarea
        id={p.id}
        ref={box}
        value={text}
        placeholder={placeholder}
        disabled={p.readOnly}
        title={p.readOnly ? READ_ONLY_WHY : undefined}
        onChange={(e) => update(e.target.value, e.target.selectionStart || e.target.value.length)}
        onKeyDown={onKeyDown}
        onBlur={() => window.setTimeout(() => setAc(null), 150)}
        aria-autocomplete="list"
        aria-expanded={!!ac}
      />
      {ac ? (
        <div className="ac" role="listbox" data-testid="autocomplete">
          <div className="cap">{ac.kind === "@" ? "Seats" : "Tickets"} · ↑↓ then Enter or Tab</div>
          {ac.items.map((i, n) => (
            <div
              key={i.k}
              role="option"
              aria-selected={n === ac.idx}
              className="opt"
              onMouseDown={(e) => {
                e.preventDefault();
                pick(n);
              }}
            >
              {i.dot ? <span className={`dot ${i.dot}`} aria-hidden="true" /> : null}
              <span className="k">{i.k}</span>
              <span className="d">{i.d}</span>
            </div>
          ))}
        </div>
      ) : null}
      <p className="hint">Enter to send · Shift+Enter for a new line</p>
      <div className="row">
        {p.fixedAbout ? (
          <span className="muted">
            about <code>{p.about}</code>
          </span>
        ) : (
          <>
            <label className="sr-only" htmlFor={`${p.id}-about`}>
              about
            </label>
            <span className="muted">about</span>
            <select id={`${p.id}-about`} value={p.about} onChange={(e) => p.onAbout?.(e.target.value)} disabled={p.readOnly}>
              <option value="">no ticket</option>
              {aboutOptions.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.id}
                </option>
              ))}
            </select>
          </>
        )}
        {text ? (
          <span className="draft" data-testid="draft">
            Draft
          </span>
        ) : null}
        <span className="grow" />
        <button
          type="button"
          className="btn primary"
          disabled={p.readOnly || busy}
          title={p.readOnly ? READ_ONLY_WHY : undefined}
          onClick={() => void submit()}
        >
          {busy ? <span className="spin" aria-hidden="true" /> : null}
          Send
        </button>
      </div>
      {p.readOnly ? <p className="why">{READ_ONLY_WHY}</p> : null}
      {sent ? (
        <p className="sent" role="status" data-testid="sent">
          Sent · to {sent.to}
          {sent.about ? ` · about ${sent.about}` : ""} · {sent.posted}
        </p>
      ) : null}
      {error ? (
        <p className="send-error" role="alert">
          Not sent: {error}
        </p>
      ) : null}
    </div>
  );
}
