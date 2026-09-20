/**
 * Time and number rendering.
 *
 * The board records UTC ISO strings and deliberately does not format them, so
 * every clock word on screen is made here, in the viewer's own zone. A stamp
 * the board never recorded is `""`, and that renders as "time not recorded" —
 * never as the epoch and never as "now".
 */

export const NOT_RECORDED = "not recorded";
export const UNKNOWN = "unknown";

/** A parsed UTC stamp, or null when the board recorded none (or a bad one). */
export function parseStamp(iso: string | null | undefined): Date | null {
  const raw = (iso || "").trim();
  if (!raw) return null;
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? null : d;
}

function hhmm(d: Date): string {
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
  );
}

/**
 * "Today at 5:37 PM" / "Yesterday at 11:04 AM" / "19 Sep at 5:37 PM", local.
 *
 * Returns "time not recorded" for an empty stamp, so a post with no `at` is
 * visibly missing a time rather than silently placed.
 */
export function localTime(iso: string | null | undefined, now: Date = new Date()): string {
  const d = parseStamp(iso);
  if (!d) return `time ${NOT_RECORDED}`;
  if (sameDay(d, now)) return `Today at ${hhmm(d)}`;
  const yesterday = new Date(now.getTime() - 24 * 3600 * 1000);
  if (sameDay(d, yesterday)) return `Yesterday at ${hhmm(d)}`;
  return `${d.toLocaleDateString(undefined, { day: "numeric", month: "short" })} at ${hhmm(d)}`;
}

/** The full local stamp plus the raw UTC, for a title attribute. */
export function exactTime(iso: string | null | undefined): string {
  const d = parseStamp(iso);
  if (!d) return `no timestamp on this record`;
  return `${d.toLocaleString()} (${d.toISOString()})`;
}

/** "14m", "2h 5m", "41s", or "" for null. Whole units only: nothing is invented. */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "";
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

/** An elapsed figure, or "not recorded" when the board has none. */
export function durationLabel(seconds: number | null | undefined): string {
  const d = duration(seconds);
  return d || NOT_RECORDED;
}

/** "3m ago" from a stamp, or "" when there is no stamp. */
export function ago(iso: string | null | undefined, now: Date = new Date()): string {
  const d = parseStamp(iso);
  if (!d) return "";
  const secs = Math.max(0, Math.round((now.getTime() - d.getTime()) / 1000));
  if (secs < 45) return `${secs}s ago`;
  return `${duration(secs)} ago`;
}

/** 41200 -> "41,200". null -> "". */
export function count(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "";
  return n.toLocaleString();
}
