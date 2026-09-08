import { BoardError } from "./errors";

/**
 * Required-top-level-key presence checks, not full recursive JSON Schema
 * validation against docs/contracts/openapi.yaml. That is a real coverage
 * gap: a wrong *nested* field (e.g. `Ticket.acceptance[0].checked` sent as a
 * string) will not be caught here, only a missing/wrong-typed top-level key
 * will. It is enough to make the ticket's required mutation checks fail
 * correctly (dropped required field, wrong scalar type at the fields this
 * client itself reads) without pulling in an OpenAPI-to-JSON-Schema
 * validator (ajv + a YAML loader) for a fixture-replay-only package. If a
 * dependent needs deeper validation, generate it from openapi.yaml directly
 * rather than hand-widening this file.
 */

type Check = [key: string, kind: "string" | "number" | "boolean" | "object" | "array"];

function assertShape(value: unknown, checks: Check[], schemaName: string): void {
  if (typeof value !== "object" || value === null) {
    throw new BoardError("invalid_response_shape", 0, `${schemaName}: response body is not an object`);
  }
  const record = value as Record<string, unknown>;
  for (const [key, kind] of checks) {
    const field = record[key];
    const ok =
      kind === "array" ? Array.isArray(field) : kind === "object" ? typeof field === "object" && field !== null : typeof field === kind;
    if (!ok) {
      throw new BoardError(
        "invalid_response_shape",
        0,
        `${schemaName}: expected "${key}" to be ${kind}, got ${field === null ? "null" : typeof field}`,
        { details: { field: key, expectedKind: kind } },
      );
    }
  }
}

const streamStatusChecks: Check[] = [["state", "string"]];

function assertStreamStatus(value: unknown, schemaName: string): void {
  assertShape(value, streamStatusChecks, `${schemaName}.stream`);
}

export function assertOverviewResponse(value: unknown): void {
  assertShape(value, [["project", "object"], ["counts", "object"], ["attention", "array"], ["stream", "object"]], "OverviewResponse");
  assertStreamStatus((value as { stream: unknown }).stream, "OverviewResponse");
}

export function assertTicketListResponse(value: unknown): void {
  assertShape(value, [["items", "array"], ["stream", "object"]], "TicketListResponse");
  assertStreamStatus((value as { stream: unknown }).stream, "TicketListResponse");
}

export function assertTicketDetailResponse(value: unknown): void {
  assertShape(
    value,
    [["ticket", "object"], ["updates", "array"], ["reviews", "array"], ["available_actions", "array"], ["stream", "object"]],
    "TicketDetailResponse",
  );
  assertTicket((value as { ticket: unknown }).ticket);
}

export function assertTicket(value: unknown): void {
  assertShape(value, [["id", "string"], ["state", "string"], ["version", "number"], ["dependency_blocked", "boolean"]], "Ticket");
}

export function assertTicketUpdate(value: unknown): void {
  assertShape(value, [["id", "string"], ["ticket_id", "string"], ["body", "string"], ["superseded", "boolean"]], "TicketUpdate");
}

export function assertReview(value: unknown): void {
  assertShape(value, [["id", "string"], ["ticket_id", "string"], ["state", "string"], ["evidence", "object"]], "Review");
}

export function assertAgentListResponse(value: unknown): void {
  assertShape(value, [["items", "array"], ["stream", "object"]], "AgentListResponse");
  assertStreamStatus((value as { stream: unknown }).stream, "AgentListResponse");
}

export function assertAgent(value: unknown): void {
  assertShape(value, [["id", "string"], ["state", "string"], ["hook_health", "object"], ["version", "number"]], "Agent");
}

export function assertCreateEnrollmentResponse(value: unknown): void {
  assertShape(
    value,
    [["enrollment_id", "string"], ["agent", "object"], ["code", "string"], ["install_command", "string"], ["config_changes", "array"]],
    "CreateEnrollmentResponse",
  );
  assertAgent((value as { agent: unknown }).agent);
}

export function assertSessionCredentialResponse(value: unknown): void {
  assertShape(value, [["agent", "object"], ["token", "string"], ["lease", "object"]], "SessionCredentialResponse");
  assertAgent((value as { agent: unknown }).agent);
}

export function assertMasterPanelResponse(value: unknown): void {
  assertShape(value, [["lease", "object"], ["queue", "array"], ["decisions", "array"], ["stream", "object"]], "MasterPanelResponse");
}

export function assertMasterLease(value: unknown): void {
  assertShape(value, [["epoch", "number"], ["paused", "boolean"], ["routing_mode", "string"]], "MasterLease");
}

export function assertAssignment(value: unknown): void {
  assertShape(value, [["id", "string"], ["ticket_id", "string"], ["agent_id", "string"], ["state", "string"], ["version", "number"]], "Assignment");
}

export function assertActivityResponse(value: unknown): void {
  assertShape(value, [["items", "array"], ["stream", "object"]], "ActivityResponse");
  assertStreamStatus((value as { stream: unknown }).stream, "ActivityResponse");
}

export function assertStreamEnvelope(value: unknown): void {
  assertShape(value, [["event_id", "string"], ["type", "string"], ["snapshot_version", "number"], ["occurred_at", "string"]], "StreamEnvelope");
}

// --------------------------------------------------------------- messages

export function assertMemberListResponse(value: unknown): void {
  assertShape(value, [["items", "array"]], "MemberListResponse");
}

export function assertMember(value: unknown): void {
  assertShape(value, [["id", "string"], ["kind", "string"], ["state", "string"], ["availability", "string"]], "Member");
}

export function assertChannelListResponse(value: unknown): void {
  assertShape(value, [["items", "array"], ["stream", "object"]], "ChannelListResponse");
  assertStreamStatus((value as { stream: unknown }).stream, "ChannelListResponse");
}

export function assertChannel(value: unknown): void {
  assertShape(value, [["id", "string"], ["name", "string"], ["kind", "string"], ["visibility", "string"]], "Channel");
}

export function assertChannelMember(value: unknown): void {
  assertShape(value, [["channel_id", "string"], ["member_id", "string"], ["subscribed", "boolean"]], "ChannelMember");
}

export function assertMessageListResponse(value: unknown): void {
  assertShape(value, [["items", "array"], ["stream", "object"]], "MessageListResponse");
  assertStreamStatus((value as { stream: unknown }).stream, "MessageListResponse");
}

export function assertMessage(value: unknown): void {
  assertShape(value, [["id", "string"], ["channel_id", "string"], ["author", "object"], ["body", "string"], ["intent", "string"]], "Message");
}

export function assertDelivery(value: unknown): void {
  assertShape(value, [["id", "string"], ["message_id", "string"], ["recipient_agent_id", "string"], ["state", "string"]], "Delivery");
}

export function assertDeliveryListResponse(value: unknown): void {
  assertShape(value, [["items", "array"]], "DeliveryListResponse");
}

export function assertSendMessageResponse(value: unknown): void {
  assertShape(value, [["message", "object"], ["deliveries", "array"]], "SendMessageResponse");
  assertMessage((value as { message: unknown }).message);
}

export function assertSendTaskResponse(value: unknown): void {
  assertShape(value, [["message", "object"], ["ticket", "object"], ["deliveries", "array"]], "SendTaskResponse");
  assertMessage((value as { message: unknown }).message);
}

/**
 * Boundary check on values this client is about to serialize onto the wire.
 * The TypeScript types already forbid an `actor` field and a string
 * `expected_version` for any caller going through the compiler; this guards
 * a caller that reaches in with `as unknown as ...` or plain JS, so a bad
 * value fails locally with a clear message instead of round-tripping to the
 * server to find out.
 */
export function assertMutationRequest(body: Record<string, unknown>, numericFields: string[], schemaName: string): void {
  if ("actor" in body) {
    throw new BoardError(
      "client_malformed_request",
      0,
      `${schemaName}: request body must never carry "actor" — the server binds the actor from the credential and rejects this with 400 malformed_request`,
      { details: { rejected_fields: ["actor"] } },
    );
  }
  for (const field of numericFields) {
    if (field in body && typeof body[field] !== "number") {
      throw new BoardError(
        "client_malformed_request",
        0,
        `${schemaName}: expected "${field}" to be a number, got ${typeof body[field]}`,
        { details: { field } },
      );
    }
  }
}
