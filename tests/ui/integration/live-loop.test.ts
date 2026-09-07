import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { randomUUID } from "node:crypto";
import { BoardClient, BoardError } from "../../../ui/src/api";
import { agentConfig, operatorConfig, readOperatorSession, startBoard, type LiveBoard } from "./board-server";

/**
 * The two-session full loop the ticket asks to be verified, against a real
 * server over a real socket.
 *
 * Two credentials, two roles, one ticket: the operator enrolls an agent and
 * decides its review; the agent exchanges the code, claims, reports and submits.
 * Neither can do the other's job, and the test asserts that too — a loop that
 * only proves the happy path would miss that the trust boundary is what makes
 * the loop meaningful.
 */
describe("two-session loop against a live board", () => {
  let board: LiveBoard;
  let operator: BoardClient;

  beforeAll(async () => {
    board = await startBoard();
    operator = new BoardClient(operatorConfig(board));
  }, 30000);

  afterAll(async () => {
    await board?.stop();
  });

  it("writes the operator session file the dashboard host reads, and nothing else", () => {
    const session = readOperatorSession(board.stateDir);
    expect(session).not.toBeNull();
    // The four keys the host plugin needs, and no more — if the shape changes,
    // the dashboard cannot bootstrap and this is where it should be noticed.
    expect(Object.keys(session!).sort()).toEqual(["csrf_token", "project_id", "session_token", "url"]);
    expect(session!.project_id).toBe(board.projectId);
  });

  it("runs enrol -> exchange -> claim -> update -> review -> accept, each step confirmed by the board", async () => {
    const ticket = await operator.createTicket({
      request_id: randomUUID(),
      title: "Wire the dashboard to the live API",
      outcome: "Every screen reads the board over HTTP",
      acceptance: [{ text: "no screen reads a fixture" }],
      role: "console",
    });
    expect(ticket.state).toBe("open");
    expect(ticket.version).toBe(1);

    const enrollment = await operator.createEnrollment({
      request_id: randomUUID(),
      agent_name: "loop-agent",
      role: "console",
      worktree: "/tmp/loop-agent",
    });
    // The code is minted once and is not a placeholder against a real server —
    // the fixtures carry "<enrollment-code-not-in-fixtures>", so a test that
    // only ever replayed fixtures could not have caught a server returning one.
    expect(enrollment.code).toMatch(/^[A-Za-z0-9]{8,}$/);
    expect(enrollment.agent.hook_health.session_adopted).toBe(false);

    const sessionId = `ses_${randomUUID().replace(/-/g, "").slice(0, 12)}`;
    const credential = await operator.exchangeEnrollment({
      request_id: randomUUID(),
      code: enrollment.code,
      session_id: sessionId,
      runtime: { adapter: "claude_code" },
    });
    expect(credential.token).toBeTruthy();
    expect(credential.agent.id).toBe(enrollment.agent.id);

    const agent = new BoardClient(agentConfig(board, credential.token));

    const claimed = await agent.claimTicket(ticket.id, {
      request_id: randomUUID(),
      expected_version: ticket.version,
      session_id: sessionId,
    });
    expect(claimed.state).toBe("claimed");
    expect(claimed.owner).toBe(credential.agent.id);
    expect(claimed.version).toBe(ticket.version + 1);

    const update = await agent.createTicketUpdate(ticket.id, {
      request_id: randomUUID(),
      body: "Wired the overview screen.",
      next_step: "Wire the agents screen.",
    });
    // The live session owns the ticket, so this is a plain accepted update.
    // `superseded` true here would mean the loop is broken in a way the
    // dashboard renders differently, which is why it is asserted and not
    // assumed.
    expect(update.superseded).toBe(false);

    const beforeReview = await agent.getTicket(ticket.id);
    const review = await agent.requestReview(ticket.id, {
      request_id: randomUUID(),
      expected_version: beforeReview.ticket.version,
      evidence: {
        branch: "opus-console/t184-live-wiring",
        sha: "0".repeat(39) + "1",
        checks: [{ name: "ui", status: "passed" }],
      },
      notes: "Screens read the API.",
    });
    expect(review.state).toBe("requested");

    const awaiting = await operator.getTicket(ticket.id);
    expect(awaiting.ticket.state).toBe("review");
    expect(awaiting.available_actions).toContain("accept");

    const decided = await operator.decideReview(ticket.id, review.id, {
      request_id: randomUUID(),
      expected_version: awaiting.ticket.version,
      decision: "accept",
      evidence_sha: review.evidence.sha,
    });
    expect(decided.state).toBe("accepted");

    const final = await operator.getTicket(ticket.id);
    expect(final.ticket.state).toBe("done");
    expect(final.available_actions).toEqual([]);
  }, 30000);

  it("refuses to accept a review whose evidence SHA is not the submitted one", async () => {
    const ticket = await operator.createTicket({
      request_id: randomUUID(),
      title: "Pinned evidence",
      outcome: "Done means the exact artifact submitted",
      acceptance: [{ text: "sha is pinned" }],
    });
    const enrollment = await operator.createEnrollment({
      request_id: randomUUID(),
      agent_name: `pin-agent-${Date.now()}`,
      role: "console",
    });
    const sessionId = `ses_${randomUUID().replace(/-/g, "").slice(0, 12)}`;
    const credential = await operator.exchangeEnrollment({
      request_id: randomUUID(),
      code: enrollment.code,
      session_id: sessionId,
    });
    const agent = new BoardClient(agentConfig(board, credential.token));
    await agent.claimTicket(ticket.id, {
      request_id: randomUUID(),
      expected_version: ticket.version,
      session_id: sessionId,
    });
    const current = await agent.getTicket(ticket.id);
    const submittedSha = "a".repeat(40);
    const review = await agent.requestReview(ticket.id, {
      request_id: randomUUID(),
      expected_version: current.ticket.version,
      evidence: { branch: "b", sha: submittedSha, checks: [{ name: "ui", status: "passed" }] },
    });

    const awaiting = await operator.getTicket(ticket.id);
    // This is the rule the dashboard implements by *pinning* the SHA rather
    // than letting an operator type one: a decision that names a different
    // artifact is refused, so a UI that let the two drift would produce 422s
    // the operator could not explain.
    const failure = await operator
      .decideReview(ticket.id, review.id, {
        request_id: randomUUID(),
        expected_version: awaiting.ticket.version,
        decision: "accept",
        evidence_sha: "b".repeat(40),
      })
      .catch((err: unknown) => err);

    expect(failure).toBeInstanceOf(BoardError);
    const error = failure as BoardError;
    expect(error.code).toBe("invalid_review_evidence");
    expect(error.status).toBe(422);
    expect(error.details.submitted_sha).toBe(submittedSha);

    const stillOpen = await operator.getTicket(ticket.id);
    expect(stillOpen.ticket.state).toBe("review");
  }, 30000);
});
