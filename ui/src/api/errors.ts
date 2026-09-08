import type { ClientErrorCode, ErrorCode, ErrorEnvelope, RequestId } from "./types";

export interface VersionConflictDetails {
  expected_version: number;
  actual_version: number;
}

/**
 * One error shape for every failure this client can raise: the server's
 * contract-shaped ErrorResponse (docs/contracts/openapi.yaml `ErrorResponse`)
 * and this client's own boundary failures (bad status code, malformed
 * response, a caller-supplied value that would fail the wire schema). Callers
 * that only care "did it work" can catch BoardError once; callers that need
 * the 409 detail can call `.versionConflict()`.
 */
export class BoardError extends Error {
  readonly code: ErrorCode | ClientErrorCode;
  readonly status: number;
  readonly requestId: RequestId | null;
  readonly details: Record<string, unknown>;

  constructor(
    code: ErrorCode | ClientErrorCode,
    status: number,
    message: string,
    options: { requestId?: RequestId | null; details?: Record<string, unknown> } = {},
  ) {
    super(message);
    this.name = "BoardError";
    this.code = code;
    this.status = status;
    this.requestId = options.requestId ?? null;
    this.details = options.details ?? {};
  }

  /**
   * The 409 `ticket_version_conflict` body carries both versions so the
   * caller can re-read in one round trip (docs/storage-notes.md #3). Returns
   * null for every other error so callers do not have to guard field shape.
   */
  versionConflict(): VersionConflictDetails | null {
    if (this.code !== "ticket_version_conflict") return null;
    const { expected_version, actual_version } = this.details;
    if (typeof expected_version === "number" && typeof actual_version === "number") {
      return { expected_version, actual_version };
    }
    return null;
  }

  static fromErrorEnvelope(body: ErrorEnvelope): BoardError {
    const { code, status, message, request_id, details } = body.error;
    return new BoardError(code, status, message, { requestId: request_id, details });
  }
}
