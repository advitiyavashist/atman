import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { randomUUID } from "node:crypto";
import { BoardClient, BoardError } from "../../../ui/src/api";
import { presentError, versionConflictDetail } from "../../../ui/src/errorCopy";
import { agentConfig, operatorConfig, startBoard, type LiveBoard } from "./board-server";

/**
 * The refusals, against a live board — because "prevent optimistic false
 * success" is a claim about what the dashboard does when the board says no, and
 * every one of these states was invisible under fixture replay.
 *
 * Each case asserts two things: that the board answers the way the dashboard's
 * copy assumes, and that the copy for that answer is the one an operator needs.
 * Testing only the first would let the error map drift from the server; testing
 * only the second would let it be confidently wrong.
 */
describe("what the board refuses, and what the dashboard says about it", () => {
  let board: LiveBoard;
  let operator: BoardClient;

  const enrol = async (name: string) => {
    const enrollment = await operator.createEnrollment({
      request_id: randomUUID(),
      agent_name: name,
      role: "console",
    });
    const sessionId = `ses_${randomUUID().replace(/-/g, "").slice(0, 12)}`;
    const credential = await operator.exchangeEnrollment({
      request_id: randomUUID(),
      code: enrollment.code,
      session_id: sessionId,
    });
    return { client: new BoardClient(agentConfig(board, credential.token)), sessionId, agent: credential.agent };
  };

  const newTicket = async (title: string) =>
    operator.createTicket({
      request_id: randomUUID(),
      title,
      outcome: "o",
      acceptance: [{ text: "a" }],
    });

  beforeAll(async () => {
    board = await startBoard();
    operator = new BoardClient(operatorConfig(board));
  }, 30000);

  afterAll(async () => {
    await board?.stop();
  });

  it("gives exactly one winner when two agents claim the same ticket", async () => {
    const ticket = await newTicket("Contended");
    const first = await enrol(`racer-a-${Date.now()}`);
    const second = await enrol(`racer-b-${Date.now()}`);

    const outcomes = await Promise.allSettled([
      first.client.claimTicket(ticket.id, {
        request_id: randomUUID(),
        expected_version: ticket.version,
        session_id: first.sessionId,
      }),
      second.client.claimTicket(ticket.id, {
        request_id: randomUUID(),
        expected_version: ticket.version,
        session_id: second.sessionId,
      }),
    ]);

    const won = outcomes.filter((o) => o.status === "fulfilled");
    const lost = outcomes.filter((o) => o.status === "rejected");
    expect(won).toHaveLength(1);
    expect(lost).toHaveLength(1);

    const error = (lost[0] as PromiseRejectedResult).reason as BoardError;
    expect(error).toBeInstanceOf(BoardError);
    // Either refusal is correct depending on which check the loser trips first;
    // both are conflicts, and the dashboard has copy for both.
    expect(["ticket_version_conflict", "ticket_already_claimed"]).toContain(error.code);
    expect(presentError(error).needsReload).toBe(true);
  }, 30000);

  it("refuses a displaced agent's update with 403 and records nothing", async () => {
    const ticket = await newTicket("Displaced");
    const owner = await enrol(`owner-${Date.now()}`);
    const other = await enrol(`other-${Date.now()}`);
    await owner.client.claimTicket(ticket.id, {
      request_id: randomUUID(),
      expected_version: ticket.version,
      session_id: owner.sessionId,
    });

    const refused = await other.client
      .createTicketUpdate(ticket.id, { request_id: randomUUID(), body: "not mine", next_step: "x" })
      .catch((err: unknown) => err);

    expect(refused).toBeInstanceOf(BoardError);
    const error = refused as BoardError;
    // 403, never 404 — the ticket plainly exists and this agent can read it.
    expect(error.code).toBe("forbidden_scope");
    expect(error.status).toBe(403);
    // The copy must say the write did not land. This is the sentence that keeps
    // a refusal from reading as a success.
    expect(presentError(error).headline).toMatch(/Nothing was recorded/);

    const detail = await operator.getTicket(ticket.id);
    expect(detail.updates.some((u) => u.body === "not mine")).toBe(false);
  }, 30000);

  it("gives both versions on a stale claim so one re-read settles it", async () => {
    const ticket = await newTicket("Stale claim");
    const first = await enrol(`stale-a-${Date.now()}`);
    const second = await enrol(`stale-b-${Date.now()}`);
    await first.client.claimTicket(ticket.id, {
      request_id: randomUUID(),
      expected_version: ticket.version,
      session_id: first.sessionId,
    });

    const refused = await second.client
      .claimTicket(ticket.id, {
        request_id: randomUUID(),
        // The version this client last saw — now one behind.
        expected_version: ticket.version,
        session_id: second.sessionId,
      })
      .catch((err: unknown) => err);

    const error = refused as BoardError;
    expect(error).toBeInstanceOf(BoardError);
    if (error.code === "ticket_version_conflict") {
      const conflict = error.versionConflict();
      expect(conflict).not.toBeNull();
      expect(conflict!.expected_version).toBe(ticket.version);
      expect(conflict!.actual_version).toBeGreaterThan(ticket.version);
      // Both numbers reach the operator, spelled out.
      expect(versionConflictDetail(error)).toMatch(/one change behind|changes behind/);
    } else {
      expect(error.code).toBe("ticket_already_claimed");
    }
  }, 30000);

  it("refuses an operator write that reuses a request id for a different body", async () => {
    const sharedId = randomUUID();
    const first = await operator.createTicket({
      request_id: sharedId,
      title: "First body",
      outcome: "o",
      acceptance: [{ text: "a" }],
    });
    expect(first.id).toBeTruthy();

    const refused = await operator
      .createTicket({ request_id: sharedId, title: "Different body", outcome: "o", acceptance: [{ text: "a" }] })
      .catch((err: unknown) => err);

    expect(refused).toBeInstanceOf(BoardError);
    const error = refused as BoardError;
    expect(error.code).toBe("request_id_reused");
    // This is why useMutation mints a fresh id per intent and only reuses one
    // when retrying that same intent: a shared id across different intents
    // makes every write after the first fail this way, which is exactly the
    // trap the fixtures carry (docs/api-notes.md).
    expect(presentError(error).needsReload).toBe(true);
  }, 30000);

  it("refuses an agent token on the four operator powers", async () => {
    const agent = await enrol(`weak-${Date.now()}`);
    const refused = await agent.client
      .createEnrollment({ request_id: randomUUID(), agent_name: "nope", role: "console" })
      .catch((err: unknown) => err);

    const error = refused as BoardError;
    expect(error).toBeInstanceOf(BoardError);
    expect(error.code).toBe("agent_token_insufficient");
    // The copy must not offer a permission the board has no way to grant: the
    // frozen security scheme has no promotion path (docs/api-notes.md gap 2).
    expect(presentError(error).headline).toMatch(/operator session/);
  }, 30000);

  it("refuses an operator write with no CSRF token", async () => {
    const noCsrf = new BoardClient({ ...operatorConfig(board), csrfToken: undefined });
    const refused = await noCsrf
      .createTicket({ request_id: randomUUID(), title: "No CSRF", outcome: "o", acceptance: [{ text: "a" }] })
      .catch((err: unknown) => err);

    const error = refused as BoardError;
    expect(error).toBeInstanceOf(BoardError);
    expect(error.code).toBe("forbidden_scope");
    // Proves the dashboard genuinely needs the CSRF token the host hands it —
    // if this ever passed, the token would be decoration.
    expect(error.message).toMatch(/CSRF/i);
  }, 30000);
});
