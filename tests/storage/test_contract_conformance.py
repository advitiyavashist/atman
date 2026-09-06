"""Storage output must validate against the frozen T-178 schemas.

T-179 builds on a contract that T-178 froze. Without this file, the storage
layer and the contract can drift apart silently and the drift only surfaces in
T-180 when routes start returning shapes the dashboard cannot read. Here, a
renamed field or a dropped required key fails immediately, in this lane.
"""

from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")
yaml = pytest.importorskip("yaml")
referencing = pytest.importorskip("referencing")

from jsonschema import Draft202012Validator  # noqa: E402
from referencing import Registry, Resource  # noqa: E402
from referencing.jsonschema import DRAFT202012  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO / "docs" / "contracts" / "openapi.yaml"
SPEC_URI = "urn:ticket-board:openapi"
SHA = "c" * 40

with SPEC_PATH.open() as _fh:
    SPEC = yaml.safe_load(_fh)

# Same registry approach as tests/test_contracts.py, so both suites resolve
# refs identically and a schema change cannot pass one bar while failing the
# other.
REGISTRY = Registry().with_resource(
    SPEC_URI, Resource.from_contents(SPEC, default_specification=DRAFT202012)
)


@pytest.fixture(scope="module")
def schemas():
    return SPEC["components"]["schemas"]


def _validate(schemas, name, instance):
    validator = Draft202012Validator(
        {"$ref": "{}#/components/schemas/{}".format(SPEC_URI, name)},
        registry=REGISTRY,
    )
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    assert not errors, "does not match the frozen {} schema:\n{}".format(
        name,
        "\n".join(
            "  {}: {}".format("/".join(str(x) for x in e.path) or "<root>", e.message)
            for e in errors
        ),
    )


def test_ticket_matches_the_contract(store, project, agent, schemas):
    store.create_ticket(project["id"], "CONF-1", "Conforming",
                        role="backend", outcome="It works",
                        acceptance=[{"text": "a", "checked": False,
                                     "checked_by": None, "checked_at": None}],
                        files=["src/"])
    ticket = store.get_ticket(project["id"], "CONF-1")
    _validate(schemas, "Ticket", ticket)


def test_claimed_ticket_matches_the_contract(store, project, agent, schemas):
    t = store.create_ticket(project["id"], "CONF-2", "Claimed")
    session = store.open_session(agent["id"], "2099-01-01T00:00:00Z")
    claimed = store.claim_ticket(project["id"], "CONF-2", agent["id"],
                                 expected_version=t["version"],
                                 session_id=session["session_id"],
                                 worktree="/repo/.worktrees/backend-1")
    _validate(schemas, "Ticket", claimed)


def test_project_matches_the_contract(store, project, schemas):
    _validate(schemas, "Project", store.get_project(project["id"]))


def test_agent_matches_the_contract(store, agent, schemas):
    _validate(schemas, "Agent", store.get_agent(agent["id"]))


def test_session_lease_matches_the_contract(store, agent, schemas):
    session = store.open_session(agent["id"], "2099-01-01T00:00:00Z")
    _validate(schemas, "SessionLease", session)


def test_ticket_update_matches_the_contract(store, project, agent, schemas):
    store.create_ticket(project["id"], "CONF-3", "Updates")
    update = store.add_update(
        project["id"], "CONF-3",
        {"type": "agent", "id": agent["id"], "display_name": "backend-1"},
        "progress", next_step="next",
    )
    _validate(schemas, "TicketUpdate", update)


def test_review_matches_the_contract(store, project, schemas):
    store.create_ticket(project["id"], "CONF-4", "Reviewable",
                        acceptance=[{"text": "a", "checked": False,
                                     "checked_by": None, "checked_at": None}])
    review = store.submit_review(
        project["id"], "CONF-4",
        {"type": "agent", "id": "agt_backend1", "display_name": "backend-1"},
        {"repository": "demo", "branch": "feature", "sha": SHA,
         "checks": [{"name": "pytest", "status": "passed"}]},
    )
    _validate(schemas, "Review", review)


def test_audit_event_matches_the_contract(store, project, schemas):
    store.create_ticket(project["id"], "CONF-5", "Audited")
    for event in store.audit_trail(project["id"]):
        _validate(schemas, "AuditEvent", event)


def test_assignment_matches_the_contract(store, project, agent, schemas):
    store.create_ticket(project["id"], "CONF-6", "Assigned")
    assignment = store.assign(project["id"], "CONF-6", agent["id"],
                              "2099-01-01T00:00:00Z", reason="routing")
    # The store returns the row; drop the storage-only project_id column, which
    # the contract's Assignment does not carry.
    assignment.pop("project_id", None)
    _validate(schemas, "Assignment", assignment)


def test_messaging_records_match_the_contract(store, project, agent, schemas):
    operator = store.create_member(
        project["id"], "human", "Operator", "owner",
        member_id="mem_operator1", availability="available",
    )
    backend = store.create_member(
        project["id"], "agent", "backend-1", "member",
        agent_id=agent["id"], member_id="mem_backend01", availability="busy",
    )
    channel = store.create_channel(
        project["id"], "work", "public", topic="Task routing",
        designated_master=agent["id"], channel_id="chn_work0001",
    )
    membership = store.add_channel_member(
        project["id"], channel["id"], operator["id"], subscribed=True
    )
    sent = store.send_message(
        project["id"], channel["id"],
        {"type": "human", "id": operator["id"], "display_name": "Operator",
         "session_id": None},
        "Can you take this?", mentions=[backend["id"]], ticket_id="DEMO-14",
        message_id="msg_root0001",
    )
    thread = store.create_thread(
        project["id"], channel["id"], sent["message"]["id"],
        ticket_id="DEMO-14", thread_id="thr_root0001",
    )
    delivery = sent["deliveries"][0]
    wake_job = store.create_wake_job(
        project["id"], sent["message"]["id"], agent["id"], delivery["id"],
        ticket_id="DEMO-14", wake_job_id="wjb_root0001",
    )
    run = store.create_run(
        project["id"], agent["id"], wake_job_id=wake_job["id"],
        ticket_claim="DEMO-14", run_id="run_root0001",
    )
    lease = store.acquire_runner_lease(
        project["id"], "rnr_root0001", agent["id"], "2099-01-01T00:00:00Z",
        allowlisted_worktree="/repos/demo/.worktrees/backend-1",
        permission_policy="allowlist",
    )
    invitation = store.create_invitation(
        project["id"], "member", "2099-01-01T00:00:00Z",
        created_by={"type": "human", "id": operator["id"],
                    "display_name": "Operator", "session_id": None},
        invitation_id="inv_root0001", code="<invite-code-not-in-fixtures>",
    )
    system_invitation = store.create_invitation(
        project["id"], "viewer", "2099-01-01T00:00:00Z",
        invitation_id="inv_root0002", code="<invite-code-not-in-fixtures>",
    )

    _validate(schemas, "Member", operator)
    _validate(schemas, "Member", backend)
    _validate(schemas, "Channel", channel)
    _validate(schemas, "ChannelMember", membership)
    _validate(schemas, "Message", sent["message"])
    _validate(schemas, "Delivery", delivery)
    _validate(schemas, "Thread", thread)
    _validate(schemas, "WakeJob", wake_job)
    _validate(schemas, "Run", run)
    _validate(schemas, "RunnerLease", lease)
    _validate(schemas, "CreateInvitationResponse", invitation)
    _validate(schemas, "CreateInvitationResponse", system_invitation)


def test_messaging_list_responses_match_the_contract(store, project, agent, schemas):
    operator = store.create_member(project["id"], "human", "Operator", "owner")
    backend = store.create_member(project["id"], "agent", "backend-1", "member",
                                  agent_id=agent["id"])
    channel = store.create_channel(project["id"], "general", "public")
    sent = store.send_message(
        project["id"], channel["id"],
        {"type": "human", "id": operator["id"], "display_name": "Operator",
         "session_id": None},
        "Hello", mentions=[backend["id"]],
    )
    delivery = sent["deliveries"][0]
    store.create_wake_job(project["id"], sent["message"]["id"], agent["id"],
                          delivery["id"])

    _validate(schemas, "MemberListResponse", store.list_members(project["id"]))
    _validate(schemas, "ChannelListResponse", store.list_channels(project["id"]))
    _validate(schemas, "MessageListResponse",
              store.list_messages(project["id"], channel["id"],
                                  member_id=operator["id"]))
    _validate(schemas, "DeliveryListResponse",
              store.list_deliveries(project["id"], sent["message"]["id"]))
    _validate(schemas, "WakeJobListResponse", store.list_wake_jobs(project["id"]))


def test_every_error_renders_the_contract_error_shape(store, project, agent, schemas):
    """Each storage error must serialize to a publishable ErrorResponse."""
    from ticket_board.storage import errors as E

    samples = [
        E.ForbiddenScope("prj_demo0001", "mem_other"),
        E.NotChannelMember("chn_private1", "mem_viewer001"),
        E.MembershipRevoked("mem_revoked01"),
        E.SenderIdentityRejected(),
        E.TicketVersionConflict("DEMO-1", 1, 2),
        E.TicketAlreadyClaimed("DEMO-1", "agt_x"),
        E.CapacityExhausted("agt_x", 1, 1),
        E.RunAlreadyActive("agt_x", "rnr_old", 1),
        E.RequestIdReused("11111111-2222-3333-4444-555555555555"),
        E.DependencyCycle(["A-1", "A-2", "A-1"]),
        E.DependencyUnmet("DEMO-1", ["DEMO-2"]),
        E.InvalidStateTransition("DEMO-1", "done", "open"),
        E.MissingAcceptanceCriteria("DEMO-1"),
        E.LegacyWriterActive("/tmp/board", "server-1"),
        E.SessionLeaseExpired("ses_abcd1234"),
        E.NotFound("nope"),
        E.MalformedRequest("nope"),
        E.InvalidReviewEvidence("nope"),
    ]
    declared = set(schemas["ErrorCode"]["enum"])
    for err in samples:
        assert err.code in declared, "{} is not a declared error code".format(err.code)
        _validate(schemas, "ErrorResponse", err.to_error_response())


# ------------------------------------------------------- the imported board

def test_every_imported_record_matches_the_contract(store, schemas, tmp_path):
    """A legacy import must not be a back door around the frozen schemas.

    The import writes tickets, agents, updates and reviews without going
    through the normal state machine, so nothing else in this suite covers what
    it produces -- and the values it handles are the awkward ones: a body over
    `outcome`'s 4000 characters, a note over `TicketUpdate.body`'s, an owner
    that started life as a bare name, a `commit` that is not a `Sha`. Every one
    of those is a way to write a row the API cannot legally serve.
    """
    import shutil

    from ticket_board.storage.legacy import import_legacy_board

    board = tmp_path / "legacy"
    shutil.copytree(
        Path(__file__).resolve().parents[1] / "data" / "legacy_board", board)
    report = import_legacy_board(store, board, project_name="Conformance")

    _validate(schemas, "Project", store.get_project(report.project_id))

    tickets = store.list_tickets(report.project_id)
    assert len(tickets) == report.imported_tickets > 0
    for ticket in tickets:
        _validate(schemas, "Ticket", ticket)
        for update in store.list_updates(report.project_id, ticket["id"]):
            _validate(schemas, "TicketUpdate", update)

    owners = {t["owner"] for t in tickets if t["owner"]}
    assert owners, "the sample board has owned tickets"
    for owner in owners:
        _validate(schemas, "Agent", store.get_agent(owner))

    reviews = store.conn.execute(
        "SELECT id FROM reviews WHERE project_id = ?", (report.project_id,)
    ).fetchall()
    assert len(reviews) == report.imported_reviews > 0
    for row in reviews:
        _validate(schemas, "Review", store.get_review(report.project_id, row["id"]))

    for event in store.audit_trail(report.project_id, limit=10000):
        _validate(schemas, "AuditEvent", event)
