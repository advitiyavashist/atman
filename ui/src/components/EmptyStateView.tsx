import type { EmptyState } from "../types";

// EmptyState.headline is server-supplied — render it rather than hardcoding
// our own wording (dependent-notes.md, T-183 section).
export function EmptyStateView({ empty, onPrimaryAction }: { empty: EmptyState; onPrimaryAction?: () => void }) {
  return (
    <div className="empty-state card">
      <h2>{empty.headline}</h2>
      {empty.detail && <p>{empty.detail}</p>}
      {empty.primary_action && (
        <button className="primary" type="button" onClick={onPrimaryAction}>
          {empty.primary_action}
        </button>
      )}
    </div>
  );
}
