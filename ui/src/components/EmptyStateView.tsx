import type { ReactNode } from "react";
import type { EmptyState } from "../types";

/** Split server `detail` on a blank line: helper, then muted honesty. */
function splitDetail(detail: string): { helper: string | null; honesty: string | null } {
  const parts = detail.split(/\n\n+/).map((p) => p.trim()).filter(Boolean);
  if (parts.length === 0) return { helper: null, honesty: null };
  if (parts.length === 1) return { helper: parts[0], honesty: null };
  return { helper: parts[0], honesty: parts.slice(1).join(" ") };
}

// EmptyState.headline is server-supplied — render it rather than hardcoding
// our own wording (dependent-notes.md, T-183 section).
export function EmptyStateView({
  empty,
  icon,
  onPrimaryAction,
}: {
  empty: EmptyState;
  icon?: ReactNode;
  onPrimaryAction?: () => void;
}) {
  const { helper, honesty } = empty.detail ? splitDetail(empty.detail) : { helper: null, honesty: null };

  return (
    <div className="empty-state empty-block" data-testid="empty-state">
      {icon != null && <span className="empty-state-icon" aria-hidden="true">{icon}</span>}
      <h2>{empty.headline}</h2>
      {helper && <p className="empty-state-detail">{helper}</p>}
      {honesty && <p className="empty-honesty">{honesty}</p>}
      {empty.primary_action && (
        <button className="primary" onClick={onPrimaryAction}>
          {empty.primary_action}
        </button>
      )}
    </div>
  );
}
