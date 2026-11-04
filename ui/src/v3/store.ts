/**
 * What lives in the browser and nowhere else (spec §9 decision 12): unsent
 * drafts, the Done fold and the panel width. Nothing about the operator's
 * view is written to the board. Every read and write is guarded, because
 * storage can be absent or refuse (private windows, cleared site data).
 */

const PREFIX = "atman.app-v3.";

export function storeGet<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function storeSet(key: string, value: unknown): void {
  try {
    if (value === undefined || value === null || value === "") localStorage.removeItem(PREFIX + key);
    else localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    /* storage refused: the view still works, it just forgets */
  }
}

/** The draft key is (board, recipient, about) (§5.6). */
export function draftKey(project: string, recipient: string, about: string): string {
  return `draft.${project}|${recipient}|${about}`;
}

export const DONE_OPEN_KEY = "done-open";
export const PANEL_WIDTH_KEY = "panel-width";
