"""T-288: four places where the running server disagrees with the E-010
contract as T-224 amended it (contract landed on main at 82ed639).

These are gaps against MERGED code, not review objections, so every test here
is written to fail on unmodified main and pass after the fix. The contract is
frozen (T-178) and T-224 amended it deliberately: where the two disagree, the
CODE is what is wrong. Nothing here edits docs/contracts/openapi.yaml.

The existing conformance suite is schema-only -- it validates the shape of what
a route returns and never asserts that a route EXISTS, that an optional field
is actually accepted, or that a documented requirement is actually enforced.
That is why all four survived it; see test_t288_conformance_classes.py for the
machine check that closes the category.
"""
import uuid

from api_client import Client, rid


def test_create_ticket_accepts_a_body_without_outcome_or_acceptance(operator):
    """Gap 1. CreateTicketRequest requires only [request_id, title]; `outcome`
    and `acceptance` are documented OPTIONAL with defaults "" and [] (T-224,
    forced by T-213's lossless legacy import -- 141 live tickets carry no
    outcome, and requiring one 422s our own board's import). The server still
    hard-requires both, so a conforming client gets 400."""
    created = operator.post("/tickets", {
        "request_id": rid(),
        "title": "A ticket from a conforming client that omits both",
    })
    assert created.status == 201, (
        "contract says outcome/acceptance are optional; server said %s %s"
        % (created.status, created.json()))
    body = created.json()
    assert body["outcome"] == "", "documented default is the empty string: %r" % body["outcome"]
    assert body["acceptance"] == [], "documented default is []: %r" % body["acceptance"]


def test_reopen_route_exists_and_returns_a_claimed_ticket_to_open(operator, enrolled, ticket):
    """Gap 2. POST /tickets/{id}/reopen is fully specified (openapi.yaml:2214,
    operationId reopenTicket, operator-gated) and has no server route at all --
    `reopen` exists only in the CLI, which bypasses the API. An API-only client,
    which is what E-010 exists to serve, cannot reopen a ticket.

    The contract's shape: an *any-state* claimed ticket returns to `open` with
    owner and owner_session cleared. That is recovery from a silent or timed-out
    claim, deliberately distinct from a review decision (a rejected review goes
    back to `claimed` with the SAME owner via decideReview)."""
    claimed = enrolled["client"].post("/tickets/%s/claim" % ticket["id"], {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"],
    })
    assert claimed.status == 200, claimed.json()
    assert claimed.json()["owner"] == enrolled["agent_id"]

    reopened = operator.post("/tickets/%s/reopen" % ticket["id"], {
        "request_id": rid(),
        "expected_version": claimed.json()["version"],
        "reason": "agent went silent; recovering the claim",
    })
    assert reopened.status == 200, (
        "reopenTicket is specified in the contract; server said %s %s"
        % (reopened.status, reopened.json()))
    body = reopened.json()
    assert body["state"] == "open", body
    assert body["owner"] is None, "owner must be cleared: %r" % body["owner"]
    assert body["owner_session"] is None, "owner_session must be cleared: %r" % body


def test_review_evidence_without_repository_is_rejected(operator, enrolled, ticket):
    """Gap 3. GitEvidence.required is [repository, branch, sha] and the schema
    text calls `repository` required in as many words -- it is T-215's fix at
    this layer, because an unqualified sha with no repository identity is
    exactly how an unrelated merge in a different repo appears to "contain"
    this evidence and closes the wrong ticket. validate.git_evidence() checks
    sha and branch and never looks at repository, so the server returns 201.

    Same class of defect as T-272, one layer up."""
    claimed = enrolled["client"].post("/tickets/%s/claim" % ticket["id"], {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"],
    })
    assert claimed.status == 200, claimed.json()

    submitted = enrolled["client"].post("/tickets/%s/reviews" % ticket["id"], {
        "request_id": rid(),
        "expected_version": claimed.json()["version"],
        "evidence": {"branch": "agent/work", "sha": "a" * 40},
        "notes": "no repository key at all",
    })
    assert submitted.status == 400, (
        "evidence.repository is required by the contract; server said %s %s"
        % (submitted.status, submitted.json()))
    assert "evidence.repository" in str(submitted.json()), submitted.json()


def test_revoke_session_lease_accepts_a_free_form_agent_id(server, project, operator):
    """Gap 4. T-224 WIDENED AgentId to ^(agt_[0-9a-z]{8,32}|[a-z][a-z0-9-]{1,31})$
    so the board's ~35 real lowercase names ARE the V1 identity, rather than
    renaming every brief, hook config and `tickets msg --to` at cutover.
    app.py's AGENT_ID_RE still hardcodes the agt_-only form and 404s before it
    ever reaches the store -- so a security-relevant verb fails CLOSED on ids
    the contract now permits."""
    agent = server.store.create_agent(project["id"], "claude-fable",
                                      role="backend", agent_id="claude-fable")
    assert agent["id"] == "claude-fable"
    session_id = "ses_" + uuid.uuid4().hex[:8]
    server.store.open_session(agent["id"], "2099-01-01T00:00:00Z", session_id=session_id)

    revoked = operator.delete("/agents/%s/session-lease" % agent["id"], {
        "request_id": rid(),
        "expected_version": agent["version"],
        "note": "operator revoking a free-form-id agent",
    })
    assert revoked.status == 200, (
        "AgentId permits the free-form name since T-224; server said %s %s"
        % (revoked.status, revoked.json()))
