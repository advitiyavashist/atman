import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TicketDetail } from "../../ui/src/screens/TicketDetail";
import { boardFetch, renderLive } from "./support/render-live";
import detailReviewPending from "../../ui/src/fixtures/data/tickets/detail-review-pending.json";
import detailSuperseded from "../../ui/src/fixtures/data/tickets/detail-superseded-update.json";

const ticketId = detailReviewPending.ticket.id;
const pending = detailReviewPending.reviews.find((r) => r.state === "requested")!;
const decisionPath = `POST /tickets/${ticketId}/reviews/${pending.id}/decision`;

describe("the rules that keep a write from looking like it landed when it did not", () => {
  it("sends the SHA pinned on the review, which the operator can see, and never a typed one", async () => {
    const user = userEvent.setup();
    const harness = boardFetch({
      [`GET /tickets/${ticketId}`]: detailReviewPending,
      [decisionPath]: { ...pending, state: "accepted", decided_at: "2026-09-06T15:00:00Z" },
    });
    renderLive(<TicketDetail ticketId={ticketId} onClose={() => {}} />, harness);

    await waitFor(() => expect(screen.getByTestId("pinned-sha")).toBeInTheDocument());
    expect(screen.getByTestId("pinned-sha")).toHaveTextContent(pending.evidence.sha);
    // There is no SHA input anywhere: the operator accepts the artifact shown.
    expect(screen.queryByLabelText(/evidence sha/i)).not.toBeInTheDocument();

    await user.click(screen.getByTestId("accept-review"));
    await waitFor(() => expect(screen.getByTestId("decision-result")).toBeInTheDocument());

    const body = harness.calls().find((c) => c.path.endsWith("/decision"))!.body as Record<string, unknown>;
    expect(body.evidence_sha).toBe(pending.evidence.sha);
    expect(body.expected_version).toBe(detailReviewPending.ticket.version);
    expect(body.decision).toBe("accept");
  });

  it("shows no success line at all when the board refuses the decision", async () => {
    const user = userEvent.setup();
    const harness = boardFetch({
      [`GET /tickets/${ticketId}`]: detailReviewPending,
      [decisionPath]: new Response(
        JSON.stringify({
          error: {
            code: "ticket_version_conflict",
            status: 409,
            message: "The ticket changed.",
            details: { expected_version: detailReviewPending.ticket.version, actual_version: detailReviewPending.ticket.version + 3 },
          },
        }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ),
    });
    renderLive(<TicketDetail ticketId={ticketId} onClose={() => {}} />, harness);

    await waitFor(() => expect(screen.getByTestId("accept-review")).toBeInTheDocument());
    await user.click(screen.getByTestId("accept-review"));

    await waitFor(() => expect(screen.getByTestId("error-notice")).toBeInTheDocument());
    expect(screen.queryByTestId("decision-result")).not.toBeInTheDocument();
    // Both versions reach the operator, so one re-read settles it.
    expect(screen.getByTestId("version-conflict-detail")).toHaveTextContent("3 changes behind");
    expect(screen.getByTestId("error-reload")).toBeInTheDocument();
  });

  it("retries a failed intent under the SAME request id, and a new intent under a new one", async () => {
    const user = userEvent.setup();
    let attempt = 0;
    const harness = boardFetch({
      [`GET /tickets/${ticketId}`]: detailReviewPending,
      [decisionPath]: () => {
        attempt += 1;
        if (attempt === 1) {
          return new Response(
            JSON.stringify({ error: { code: "rate_limited", status: 429, message: "Slow down." } }),
            { status: 429, headers: { "Content-Type": "application/json" } },
          );
        }
        return { ...pending, state: "accepted", decided_at: "2026-09-06T15:00:00Z" };
      },
    });
    renderLive(<TicketDetail ticketId={ticketId} onClose={() => {}} />, harness);

    await waitFor(() => expect(screen.getByTestId("accept-review")).toBeInTheDocument());
    await user.click(screen.getByTestId("accept-review"));
    await waitFor(() => expect(screen.getByTestId("error-notice")).toBeInTheDocument());
    await user.click(screen.getByTestId("accept-review"));
    await waitFor(() => expect(screen.getByTestId("decision-result")).toBeInTheDocument());

    const decisions = harness.calls().filter((c) => c.path.endsWith("/decision"));
    expect(decisions).toHaveLength(2);
    const firstId = (decisions[0].body as Record<string, string>).request_id;
    const secondId = (decisions[1].body as Record<string, string>).request_id;
    // The point of an idempotency key is that a retry of the same intent is
    // recognised as the same act. Minting a new id per retry defeats it.
    expect(secondId).toBe(firstId);
  });

  it("refuses to accept while a check has failed, rather than being refused by the board", async () => {
    const failing = {
      ...detailReviewPending,
      reviews: detailReviewPending.reviews.map((r) =>
        r.id === pending.id
          ? { ...r, evidence: { ...r.evidence, checks: [{ name: "tests", status: "failed" }] } }
          : r,
      ),
    };
    const harness = boardFetch({ [`GET /tickets/${ticketId}`]: failing });
    renderLive(<TicketDetail ticketId={ticketId} onClose={() => {}} />, harness);

    await waitFor(() => expect(screen.getByTestId("failing-checks")).toBeInTheDocument());
    expect(screen.getByTestId("accept-review")).toBeDisabled();
    // Reject stays available: a failed check is a reason to reject, not a
    // reason to be unable to do anything.
    expect(screen.getByTestId("reject-review")).toBeEnabled();
  });

  it("renders a superseded update as kept-but-ineffective, not as a plain success", async () => {
    const harness = boardFetch({ [`GET /tickets/${detailSuperseded.ticket.id}`]: detailSuperseded });
    renderLive(<TicketDetail ticketId={detailSuperseded.ticket.id} onClose={() => {}} />, harness);

    await waitFor(() => expect(screen.getByTestId("superseded-tag")).toBeInTheDocument());
    // Both halves: it is in the trail, and it changed nothing.
    expect(screen.getByText(/no longer owns the ticket/)).toBeInTheDocument();
    expect(screen.getByText(/did not change ticket state/)).toBeInTheDocument();
  });

  it("says agent-only actions belong to the agent instead of offering a button that cannot work", async () => {
    const claimable = {
      ...detailReviewPending,
      available_actions: ["claim", "update"],
    };
    const harness = boardFetch({ [`GET /tickets/${ticketId}`]: claimable });
    renderLive(<TicketDetail ticketId={ticketId} onClose={() => {}} />, harness);

    await waitFor(() => expect(screen.getByTestId("agent-only-actions")).toBeInTheDocument());
    // The operator session has no runtime session to claim through, so a Claim
    // button here could only ever produce a 403.
    expect(screen.getByTestId("agent-only-actions")).toHaveTextContent(/no runtime session/);
    expect(screen.queryByRole("button", { name: "Claim" })).not.toBeInTheDocument();
  });
  it("renders a review whose evidence carries no checks at all, instead of crashing", async () => {
    // GitEvidence.required is [branch, sha]: a review may legally carry no
    // `checks` key, and a live board really does echo the evidence back without
    // one. ui/src/types.ts typed `checks` as required until T-184, so
    // `evidence.checks.map(...)` threw on a perfectly valid review. Verified
    // against a real server before this test was written, not inferred.
    const checkless = {
      ...detailReviewPending,
      ticket: { ...detailReviewPending.ticket, evidence: { branch: "b", sha: "c".repeat(40) } },
      reviews: detailReviewPending.reviews.map((r) =>
        r.id === pending.id ? { ...r, evidence: { branch: "b", sha: "c".repeat(40) } } : r,
      ),
    };
    const harness = boardFetch({ [`GET /tickets/${ticketId}`]: checkless });
    renderLive(<TicketDetail ticketId={ticketId} onClose={() => {}} />, harness);

    await waitFor(() => expect(screen.getByTestId("review-checks")).toHaveTextContent("none reported"));
    // With no checks there is nothing failing, so accepting stays available.
    expect(screen.getByTestId("accept-review")).toBeEnabled();
    expect(screen.queryByTestId("failing-checks")).not.toBeInTheDocument();
  });
});
