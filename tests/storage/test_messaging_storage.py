import sqlite3

import pytest

from ticket_board.storage import (
    ForbiddenScope,
    InvalidStateTransition,
    MembershipRevoked,
    NotChannelMember,
    RequestIdReused,
    TicketVersionConflict,
)


REQUEST_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture()
def messaging_setup(store, project, agent):
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
        channel_id="chn_work0001",
    )
    store.add_channel_member(project["id"], channel["id"], operator["id"])
    store.add_channel_member(project["id"], channel["id"], backend["id"])
    author = {
        "type": "human",
        "id": operator["id"],
        "display_name": "Operator",
        "session_id": None,
    }
    return project, agent, operator, backend, channel, author


def test_send_message_writes_message_and_deliveries_in_one_transaction(
    store, messaging_setup
):
    project, agent, operator, backend, channel, author = messaging_setup
    sent = store.send_message(
        project["id"], channel["id"], author, "Please take DEMO-14.",
        mentions=[backend["id"]], ticket_id="DEMO-14", request_id=REQUEST_ID,
    )

    assert sent["message"]["body"] == "Please take DEMO-14."
    assert sent["message"]["author"] == author
    assert sent["deliveries"][0]["recipient_agent_id"] == agent["id"]
    assert sent["deliveries"][0]["state"] == "queued"

    replay = store.send_message(
        project["id"], channel["id"], author, "Please take DEMO-14.",
        mentions=[backend["id"]], ticket_id="DEMO-14", request_id=REQUEST_ID,
    )
    assert replay == sent

    with pytest.raises(RequestIdReused):
        store.send_message(
            project["id"], channel["id"], author, "Different body.",
            mentions=[backend["id"]], ticket_id="DEMO-14",
            request_id=REQUEST_ID,
        )

    assert len(store.list_messages(project["id"], channel["id"])["items"]) == 1
    message_audits = [
        e for e in store.audit_trail(project["id"])
        if e["action"] == "message.create"
    ]
    assert len(message_audits) == 1


def test_message_and_outbox_rows_roll_back_together(store, messaging_setup, monkeypatch):
    project, agent, operator, backend, channel, author = messaging_setup

    def fail_delivery(*args, **kwargs):
        raise RuntimeError("delivery write failed")

    monkeypatch.setattr(store, "_insert_delivery", fail_delivery)

    with pytest.raises(RuntimeError):
        store.send_message(
            project["id"], channel["id"], author, "This must roll back.",
            mentions=[backend["id"]],
        )

    assert store.list_messages(project["id"], channel["id"])["items"] == []
    assert store.list_deliveries(project["id"], "msg_missing")["items"] == []


def test_private_channel_access_is_project_scoped(store, project, agent):
    project_b = store.create_project("Other")
    operator_a = store.create_member(project["id"], "human", "Operator", "owner")
    outsider_b = store.create_member(project_b["id"], "human", "Other", "owner")
    private_b = store.create_channel(project_b["id"], "incidents", "private")
    store.add_channel_member(project_b["id"], private_b["id"], outsider_b["id"])

    with pytest.raises(ForbiddenScope):
        store.get_channel(project_b["id"], private_b["id"], member_id=operator_a["id"])


def test_private_channel_requires_membership(store, project):
    operator = store.create_member(project["id"], "human", "Operator", "owner")
    viewer = store.create_member(project["id"], "human", "Viewer", "viewer")
    private = store.create_channel(project["id"], "incidents", "private")
    store.add_channel_member(project["id"], private["id"], operator["id"])

    with pytest.raises(NotChannelMember):
        store.get_channel(project["id"], private["id"], member_id=viewer["id"])

    store.add_channel_member(project["id"], private["id"], viewer["id"])
    assert store.get_channel(project["id"], private["id"], member_id=viewer["id"])["id"] \
        == private["id"]


def test_private_channel_membership_is_scoped_by_project(store, project):
    member = store.create_member(project["id"], "human", "Operator", "owner")
    private = store.create_channel(project["id"], "incidents", "private")
    other_project = store.create_project("Other")
    store.conn.execute(
        "INSERT INTO channel_members (project_id, channel_id, member_id, subscribed, joined_at)"
        " VALUES (?, ?, ?, 1, ?)",
        (other_project["id"], private["id"], member["id"], "2026-09-06T14:32:00Z"),
    )

    with pytest.raises(NotChannelMember):
        store.get_channel(project["id"], private["id"], member_id=member["id"])


def test_revoked_member_cannot_read_or_be_added(store, project):
    operator = store.create_member(project["id"], "human", "Operator", "owner")
    channel = store.create_channel(project["id"], "incidents", "private")
    store.add_channel_member(project["id"], channel["id"], operator["id"])
    store.revoke_member(project["id"], operator["id"],
                        expected_version=operator["version"])

    with pytest.raises(MembershipRevoked):
        store.get_channel(project["id"], channel["id"], member_id=operator["id"])

    with pytest.raises(MembershipRevoked):
        store.add_channel_member(project["id"], channel["id"], operator["id"])


def test_message_body_is_immutable(store, messaging_setup):
    project, agent, operator, backend, channel, author = messaging_setup
    sent = store.send_message(
        project["id"], channel["id"], author, "Original", mentions=[backend["id"]]
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "UPDATE messages SET body = ? WHERE id = ?",
            ("edited", sent["message"]["id"]),
        )

    assert store.list_messages(project["id"], channel["id"])["items"][0]["body"] \
        == "Original"


def test_list_messages_filters_by_project_even_with_cross_scoped_channel_row(
    store, messaging_setup
):
    project, _agent, _operator, _backend, channel, author = messaging_setup
    sent = store.send_message(project["id"], channel["id"], author, "Legitimate")
    other_project = store.create_project("Other")
    store.conn.execute(
        "INSERT INTO messages (id, project_id, channel_id, author, body, intent,"
        " mentions, version, created_at) VALUES (?, ?, ?, ?, ?, 'message', '[]', 1, ?)",
        (
            "msg_crossproject1",
            other_project["id"],
            channel["id"],
            '{"type":"system","id":"system"}',
            "wrong project",
            "2026-09-06T14:32:00Z",
        ),
    )

    listed = store.list_messages(project["id"], channel["id"])["items"]
    assert [item["id"] for item in listed] == [sent["message"]["id"]]


def test_delivery_transitions_are_closed_and_versioned(store, messaging_setup):
    project, agent, operator, backend, channel, author = messaging_setup
    sent = store.send_message(
        project["id"], channel["id"], author, "Please take it.",
        mentions=[backend["id"]],
    )
    delivery = sent["deliveries"][0]

    with pytest.raises(InvalidStateTransition):
        store.transition_delivery(
            project["id"], delivery["id"], "responded",
            expected_version=delivery["version"],
        )

    started = store.transition_delivery(
        project["id"], delivery["id"], "started",
        expected_version=delivery["version"], run_id="run_demo0001",
    )
    assert started["state"] == "started"
    assert started["version"] == delivery["version"] + 1

    with pytest.raises(TicketVersionConflict) as exc:
        store.transition_delivery(
            project["id"], delivery["id"], "responded",
            expected_version=delivery["version"],
        )
    assert exc.value.details["expected_version"] == delivery["version"]
    assert exc.value.details["actual_version"] == started["version"]

    with pytest.raises(InvalidStateTransition):
        store.transition_delivery(
            project["id"], delivery["id"], "queued",
            expected_version=started["version"],
        )


def test_wake_jobs_dedupe_by_message_and_recipient(store, messaging_setup):
    project, agent, operator, backend, channel, author = messaging_setup
    sent = store.send_message(
        project["id"], channel["id"], author, "Wake backend.",
        mentions=[backend["id"]],
    )
    delivery = sent["deliveries"][0]
    first = store.create_wake_job(
        project["id"], sent["message"]["id"], agent["id"], delivery["id"]
    )
    second = store.create_wake_job(
        project["id"], sent["message"]["id"], agent["id"], delivery["id"]
    )

    assert second == first
    assert store.list_wake_jobs(project["id"])["items"] == [first]


def test_run_events_require_started_session_and_preserve_visible_pauses(
    store, project, agent
):
    """The reporter's session is now separate from the run's own (T-188).

    `reporter_session_id` is the supervisor's, bound from its credential at the
    route; `session_id` is the child session the run executes in. This test
    supplies both -- the unattributed and superseded paths have their own
    coverage in tests/runners/test_run_event_attribution.py.
    """
    supervisor = store.open_session(agent["id"], "2099-01-01T00:00:00Z")
    run = store.create_run(project["id"], agent["id"])

    with pytest.raises(InvalidStateTransition):
        store.record_run_event(
            project["id"], run["id"], "started",
            expected_version=run["version"],
            reporter_session_id=supervisor["session_id"],
        )

    started = store.record_run_event(
        project["id"], run["id"], "started",
        expected_version=run["version"],
        reporter_session_id=supervisor["session_id"],
        session_id="ses_a1b2c3d4", ticket_claim="DEMO-14",
    )
    assert started["state"] == "running"
    assert started["started_at"] is not None

    paused = store.record_run_event(
        project["id"], run["id"], "needs_approval",
        expected_version=started["version"],
        reporter_session_id=supervisor["session_id"],
        reason="Permission request needs approval.",
    )
    assert paused["state"] == "paused"
    assert paused["needs_approval"] is True
    assert paused["terminal_reason"] == "Permission request needs approval."

    canceled = store.cancel_run(
        project["id"], run["id"], expected_version=paused["version"],
        reason="Operator canceled.",
    )
    assert canceled["state"] == "canceled"
    assert canceled["ended_at"] is not None
