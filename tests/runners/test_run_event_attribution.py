"""T-239's three-state rule, applied to run events.

The hole T-239 closed in ticket updates has the identical shape here: an
optional identity field feeding a safeguard means a displaced or anonymous
writer is recorded as an ordinary one simply by omitting the field. The answer
is behavioural, not schematic -- the frozen contract keeps `session_id`
optional and nullable, and absence is treated as *not attributed* rather than
as clean.

What is different here, and worth being precise about, is that two different
sessions meet on one call:

  reporter_session_id   the supervisor's, bound from its agent credential
  session_id (body)     the child Claude session the run executes in

Only the first is an identity claim, so only the first is credential-bound.
The second cannot be -- it does not exist until the supervisor mints it -- so
it is bound the other way, write-once.
"""

from __future__ import annotations

import pytest

from tests.runner_helpers import make_wake_job  # noqa: F401
from ticket_board.runners import ApiError
from ticket_board.storage.errors import InvalidStateTransition


@pytest.fixture()
def run(server, project, agent, client):
    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    job = make_wake_job(server, project, agent["agent_id"])
    client.jobs("rnr_aaaa1111", wait_seconds=0)
    return {"id": "run_" + job["id"][4:], "job": job}


def audit_actions(server, project, run_id):
    rows = server.store.conn.execute(
        "SELECT action FROM audit_events WHERE project_id = ? AND subject_id = ?"
        " ORDER BY seq", (project["id"], run_id)).fetchall()
    return [r["action"] for r in rows]


def test_an_attributed_reporter_moves_the_run(server, project, agent, client, run):
    started = client.run_event(run["id"], "starting", expected_version=1,
                               session_id="ses_child001")
    assert started["state"] == "starting"
    assert started["session_id"] == "ses_child001"
    running = client.run_event(run["id"], "started",
                               expected_version=started["version"])
    assert running["state"] == "running"
    assert running["started_at"] is not None
    assert "run.event" in audit_actions(server, project, run["id"])


def test_an_unattributed_reporter_cannot_paint_the_run_green(
        server, project, agent, client, run):
    """An agent token with no session at all is the anonymous-writer shape."""
    store = server.store
    # Give the run a retained child session the honest way first.
    client.run_event(run["id"], "starting", expected_version=1,
                     session_id="ses_child001")
    before = store.get_run(project["id"], run["id"])

    denied = store.record_run_event(
        project["id"], run["id"], "started",
        expected_version=before["version"],
        reporter_session_id=None, session_id="ses_child001")

    assert denied["state"] == before["state"] == "starting"
    assert denied["started_at"] is None
    assert denied["version"] == before["version"]      # not even a version bump
    assert "run.event.unattributed" in audit_actions(server, project, run["id"])
    assert "run.event" not in \
        audit_actions(server, project, run["id"])[-1:]


def test_an_unattributed_reporter_cannot_close_the_run(server, project, agent,
                                                       client, run):
    store = server.store
    client.run_event(run["id"], "starting", expected_version=1,
                     session_id="ses_child001")
    started = client.run_event(run["id"], "started", expected_version=2)

    denied = store.record_run_event(
        project["id"], run["id"], "responded",
        expected_version=started["version"], reporter_session_id=None)
    assert denied["state"] == "running"
    assert denied["ended_at"] is None
    # And the job it came from is still open, so the work is not lost either.
    states = {j["id"]: j["state"]
              for j in store.list_wake_jobs(project["id"])["items"]}
    assert states[run["job"]["id"]] == "leased"


def test_a_revoked_reporter_session_is_superseded_not_ordinary(
        server, project, agent, client, run):
    store = server.store
    client.run_event(run["id"], "starting", expected_version=1,
                     session_id="ses_child001")
    before = store.get_run(project["id"], run["id"])
    store.revoke_session(agent["session_id"], note="displaced")

    denied = store.record_run_event(
        project["id"], run["id"], "started",
        expected_version=before["version"],
        reporter_session_id=agent["session_id"])
    assert denied["state"] == "starting"
    assert denied["version"] == before["version"]
    assert "run.event.superseded" in audit_actions(server, project, run["id"])


def test_the_child_session_is_write_once(server, project, agent, client, run):
    """A later event may not silently rebind which session a run is executing.

    That is the displacement shape for the child half: the run says it is
    session A, something reports as session B, and without this rule the run
    quietly becomes B's -- with A's process still running.
    """
    store = server.store
    client.run_event(run["id"], "starting", expected_version=1,
                     session_id="ses_child001")
    before = store.get_run(project["id"], run["id"])

    denied = store.record_run_event(
        project["id"], run["id"], "started",
        expected_version=before["version"],
        reporter_session_id=agent["session_id"], session_id="ses_child999")
    assert denied["session_id"] == "ses_child001"
    assert denied["state"] == "starting"
    assert "run.event.superseded" in audit_actions(server, project, run["id"])


def test_started_with_no_runtime_session_stays_a_422_for_everyone(
        server, project, agent, client, run):
    """The contract names 422 here, and the answer must not depend on who asks.

    Ordering matters: if the attribution gate ran first, an unattributed caller
    would get a 200-with-no-effect where every other caller gets the documented
    refusal -- a different answer to the same illegal request.
    """
    with pytest.raises(ApiError) as caught:
        client.run_event(run["id"], "started", expected_version=1)
    assert caught.value.status == 422
    assert caught.value.code == "invalid_state_transition"

    with pytest.raises(InvalidStateTransition):
        server.store.record_run_event(project["id"], run["id"], "started",
                                      expected_version=1,
                                      reporter_session_id=None)


def test_the_route_never_reads_the_reporter_from_the_body(server, project,
                                                          agent, client, run):
    """There is no body field that can name the reporter, and adding one fails.

    `RunEventRequest` is `additionalProperties: false`, so a caller cannot
    smuggle a `reporter_session_id` in; and the identifying-field guard rejects
    the usual spellings outright.
    """
    status, payload = client._call(
        "POST", "/runs/{}/events".format(run["id"]),
        body={"request_id": "11111111-1111-4111-8111-111111111111",
              "expected_version": 1, "event": "starting",
              "reporter_session_id": "ses_forged1"})
    assert status == 400
    assert payload["error"]["details"]["rejected_fields"] == \
        ["reporter_session_id"]

    status, payload = client._call(
        "POST", "/runs/{}/events".format(run["id"]),
        body={"request_id": "22222222-2222-4222-8222-222222222222",
              "expected_version": 1, "event": "starting",
              "actor": {"type": "agent", "id": agent["agent_id"]}})
    assert status == 400
    assert "Actor is bound from the credential" in payload["error"]["message"]
