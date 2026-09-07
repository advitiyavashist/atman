import type { BoardError } from "../api";
import { presentError, versionConflictDetail } from "../errorCopy";

/**
 * A refused write, shown where the control that caused it is.
 *
 * Two things it deliberately does not do: it does not disappear on its own,
 * and it never sits next to a success state. A toast that fades is the wrong
 * shape for "your write did not happen" — the operator may not have been
 * looking.
 */
export function ErrorNotice({
  error,
  onRetry,
  onReload,
}: {
  error: BoardError;
  onRetry?: () => void;
  onReload?: () => void;
}) {
  const presented = presentError(error);
  const conflict = versionConflictDetail(error);

  return (
    <div className="notice error" role="alert" data-testid="error-notice" data-error-code={error.code}>
      <strong>{presented.headline}</strong>
      {conflict && <div data-testid="version-conflict-detail">{conflict}</div>}
      <small>
        {presented.detail}
        {error.requestId ? ` · request ${error.requestId}` : ""}
      </small>
      <div className="dialog-actions" style={{ justifyContent: "flex-start", marginTop: 8 }}>
        {presented.needsReload && onReload && (
          <button onClick={onReload} data-testid="error-reload">
            Reload from the board
          </button>
        )}
        {presented.retryable && onRetry && (
          <button onClick={onRetry} data-testid="error-retry">
            Retry
          </button>
        )}
      </div>
    </div>
  );
}
